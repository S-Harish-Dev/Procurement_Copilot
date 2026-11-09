//! Internal OpenAI-compatible endpoint (`:8081/v1/chat/completions`) used by the brain's agent.
//!
//! Every LLM call in the system goes through the gateway's router — one place for fallback,
//! breakers, quotas, cost metrics and tracing. The brain sets the tier with `x-model-tier`
//! (decided by the Jev router) and authenticates with a shared internal token.

use axum::Json;
use axum::extract::State;
use axum::http::HeaderMap;
use axum::response::sse::{Event, Sse};
use axum::response::{IntoResponse, Response};
use futures::StreamExt;
use llm::{StreamEvent, Tier};
use serde_json::Value;

use crate::http::ApiError;
use crate::state::SharedState;

fn constant_time_eq(a: &[u8], b: &[u8]) -> bool {
    a.len() == b.len() && a.iter().zip(b).fold(0u8, |acc, (x, y)| acc | (x ^ y)) == 0
}

pub async fn chat_completions(
    State(s): State<SharedState>,
    headers: HeaderMap,
    Json(mut body): Json<Value>,
) -> Result<Response, ApiError> {
    let token = headers
        .get("x-internal-token")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");
    let bearer = headers
        .get("authorization")
        .and_then(|v| v.to_str().ok())
        .and_then(|v| v.strip_prefix("Bearer "))
        .unwrap_or("");
    let presented = if token.is_empty() { bearer } else { token };
    if !constant_time_eq(presented.as_bytes(), s.cfg.internal_token.as_bytes()) {
        return Err(ApiError::Unauthorized);
    }
    let tier = headers
        .get("x-model-tier")
        .and_then(|v| v.to_str().ok())
        .and_then(Tier::parse)
        .unwrap_or(Tier::Cheap);
    let stream = body.get("stream").and_then(Value::as_bool).unwrap_or(false);
    // The router owns `model`/`stream`; whatever the client sent is ignored.
    if let Some(obj) = body.as_object_mut() {
        obj.remove("model");
        obj.remove("stream");
        obj.remove("stream_options");
    }

    if !stream {
        let (served, resp) = s
            .llm
            .chat(tier, body)
            .await
            .map_err(|_| ApiError::Unavailable("llm"))?;
        let mut r = Json(resp).into_response();
        r.headers_mut().insert(
            "x-served-by",
            served.provider.parse().expect("header value"),
        );
        return Ok(r);
    }
    let (served, mut events) = s
        .llm
        .chat_stream(tier, body)
        .await
        .map_err(|_| ApiError::Unavailable("llm"))?;
    let out = async_stream::stream! {
        while let Some(ev) = events.next().await {
            match ev {
                Ok(StreamEvent::Chunk(c)) => yield Ok::<_, std::convert::Infallible>(Event::default().data(c.to_string())),
                Ok(StreamEvent::End { .. }) => {}
                Err(e) => {
                    tracing::warn!(error = %e, "internal stream error");
                    break;
                }
            }
        }
        yield Ok(Event::default().data("[DONE]"));
    };
    let mut r = Sse::new(out).into_response();
    r.headers_mut().insert(
        "x-served-by",
        served.provider.parse().expect("header value"),
    );
    Ok(r)
}

#[cfg(test)]
mod tests {
    use super::constant_time_eq;

    #[test]
    fn token_compare() {
        assert!(constant_time_eq(b"abc", b"abc"));
        assert!(!constant_time_eq(b"abc", b"abd"));
        assert!(!constant_time_eq(b"abc", b"abcd"));
    }
}
