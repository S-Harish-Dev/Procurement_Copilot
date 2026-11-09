//! OpenAI-compatible fake LLM for load tests, chaos drills and offline demos.
//!
//! Behaviour (deterministic, no model):
//! - **RAG prompts**: if the conversation contains `<document id="S#" ...>` blocks, the answer is
//!   the first sentence of the document sharing the most words with the user's question, followed
//!   by `[S#]`. Answers are therefore grounded and citations validate end to end.
//! - **Agent prompts**: a user message containing `[[call tool_name {json args}]]` yields a real
//!   `tool_calls` response; once a tool result is in the conversation, the next turn is a short
//!   final answer quoting it. This drives the agent/MCP path in tests without an LLM.
//! - Abstains with `NO_ANSWER` when there are no documents and no tools.
//!
//! - **Mock Jev** (`POST /v1/systemone`): schema-correct TypeSafe answers from transparent
//!   heuristics (keyword intent rules, word-overlap relevance, regex injection checks) so the full
//!   pipeline runs offline. Numbers measured against it are labelled "mock gate", never "Jev".
//!
//! Env knobs: MOCK_TTFT_MS (150), MOCK_TOKEN_MS (15), MOCK_MAX_TOKENS (120),
//! MOCK_FAIL_RATE (0.0, returns 503), MOCK_JEV_LATENCY_MS (60), MOCK_PORT (8090).

use std::convert::Infallible;
use std::sync::OnceLock;
use std::time::Duration;

use axum::Router;
use axum::extract::Json;
use axum::http::StatusCode;
use axum::response::sse::{Event, Sse};
use axum::response::{IntoResponse, Response};
use axum::routing::{get, post};
use futures::Stream;
use rand::RngExt;
use regex::Regex;
use serde_json::{Value, json};

mod jev;

struct Knobs {
    ttft: Duration,
    per_token: Duration,
    max_tokens: usize,
    fail_rate: f64,
}

fn knobs() -> &'static Knobs {
    static K: OnceLock<Knobs> = OnceLock::new();
    K.get_or_init(|| {
        let num = |k: &str, d: f64| {
            std::env::var(k)
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(d)
        };
        Knobs {
            ttft: Duration::from_millis(num("MOCK_TTFT_MS", 150.0) as u64),
            per_token: Duration::from_millis(num("MOCK_TOKEN_MS", 15.0) as u64),
            max_tokens: num("MOCK_MAX_TOKENS", 120.0) as usize,
            fail_rate: num("MOCK_FAIL_RATE", 0.0),
        }
    })
}

enum Reply {
    Text(String),
    ToolCall { name: String, arguments: String },
}

fn text_of(m: &Value) -> String {
    match &m["content"] {
        Value::String(s) => s.clone(),
        Value::Array(parts) => parts
            .iter()
            .filter_map(|p| p["text"].as_str())
            .collect::<Vec<_>>()
            .join("\n"),
        _ => String::new(),
    }
}

fn words(s: &str) -> std::collections::HashSet<String> {
    s.split(|c: char| !c.is_alphanumeric())
        .filter(|w| w.len() > 3)
        .map(str::to_lowercase)
        .collect()
}

fn decide(body: &Value) -> Reply {
    static CALL: OnceLock<Regex> = OnceLock::new();
    static DOC: OnceLock<Regex> = OnceLock::new();
    let call =
        CALL.get_or_init(|| Regex::new(r"\[\[call\s+(\w+)\s+(\{.*?\})\s*\]\]").expect("regex"));
    let doc = DOC.get_or_init(|| {
        Regex::new(r#"(?s)<document id="(S\d+)"[^>]*>(.*?)</document>"#).expect("regex")
    });

    let messages = body["messages"].as_array().cloned().unwrap_or_default();
    let last = messages.last().cloned().unwrap_or(Value::Null);

    // A tool result is the latest turn: summarise it.
    if last["role"] == "tool" {
        let result: String = text_of(&last).chars().take(400).collect();
        return Reply::Text(format!("Done. Result: {result}"));
    }
    let user = messages
        .iter()
        .rev()
        .find(|m| m["role"] == "user")
        .map(text_of)
        .unwrap_or_default();
    if let Some(c) = call.captures(&user)
        && body
            .get("tools")
            .is_some_and(|t| t.as_array().is_some_and(|a| !a.is_empty()))
    {
        return Reply::ToolCall {
            name: c[1].to_owned(),
            arguments: c[2].to_owned(),
        };
    }
    let all: String = messages.iter().map(text_of).collect::<Vec<_>>().join("\n");
    let question = words(&user);
    let best = doc
        .captures_iter(&all)
        .map(|c| (c[1].to_owned(), c[2].trim().to_owned()))
        .max_by_key(|(_, text)| words(text).intersection(&question).count());
    match best {
        Some((id, text)) => {
            // Quote the sentence that shares the most words with the question.
            let sentence = text
                .split_inclusive(['.', '\n'])
                .filter(|s| s.trim().len() > 20)
                .max_by_key(|s| words(s).intersection(&question).count())
                .unwrap_or(&text)
                .trim();
            Reply::Text(format!("According to the documents, {sentence} [{id}]"))
        }
        None => Reply::Text("NO_ANSWER".into()),
    }
}

fn tokens(text: &str) -> Vec<String> {
    let mut out: Vec<String> = text.split_inclusive(' ').map(str::to_owned).collect();
    out.truncate(knobs().max_tokens);
    out
}

fn chunk(id: &str, model: &str, delta: Value, finish: Option<&str>) -> Value {
    json!({"id": id, "object": "chat.completion.chunk", "model": model,
           "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})
}

fn sse_stream(
    reply: Reply,
    model: String,
    prompt_tokens: u64,
) -> impl Stream<Item = Result<Event, Infallible>> {
    async_stream::stream! {
        let id = "chatcmpl-mock";
        tokio::time::sleep(knobs().ttft).await;
        let completion_tokens;
        match reply {
            Reply::Text(text) => {
                let toks = tokens(&text);
                completion_tokens = toks.len() as u64;
                yield Ok(Event::default().data(chunk(id, &model, json!({"role": "assistant", "content": ""}), None).to_string()));
                for t in toks {
                    yield Ok(Event::default().data(chunk(id, &model, json!({"content": t}), None).to_string()));
                    tokio::time::sleep(knobs().per_token).await;
                }
                yield Ok(Event::default().data(chunk(id, &model, json!({}), Some("stop")).to_string()));
            }
            Reply::ToolCall { name, arguments } => {
                completion_tokens = 10;
                let delta = json!({"role": "assistant", "tool_calls": [{"index": 0, "id": "call_mock_1", "type": "function",
                                    "function": {"name": name, "arguments": arguments}}]});
                yield Ok(Event::default().data(chunk(id, &model, delta, Some("tool_calls")).to_string()));
            }
        }
        let usage = json!({"id": id, "object": "chat.completion.chunk", "model": model, "choices": [],
                           "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens}});
        yield Ok(Event::default().data(usage.to_string()));
        yield Ok(Event::default().data("[DONE]"));
    }
}

async fn completions(Json(body): Json<Value>) -> Response {
    if knobs().fail_rate > 0.0 && rand::rng().random_bool(knobs().fail_rate.min(1.0)) {
        return (StatusCode::SERVICE_UNAVAILABLE, "mock failure").into_response();
    }
    let model = body["model"].as_str().unwrap_or("mock").to_owned();
    let prompt_tokens = (serde_json::to_string(&body["messages"])
        .unwrap_or_default()
        .len()
        / 4) as u64;
    let reply = decide(&body);
    if body["stream"].as_bool().unwrap_or(false) {
        return Sse::new(sse_stream(reply, model, prompt_tokens)).into_response();
    }
    tokio::time::sleep(knobs().ttft).await;
    let message = match reply {
        Reply::Text(t) => json!({"role": "assistant", "content": t}),
        Reply::ToolCall { name, arguments } => {
            json!({"role": "assistant", "content": null, "tool_calls": [
            {"id": "call_mock_1", "type": "function", "function": {"name": name, "arguments": arguments}}]})
        }
    };
    let finish = if message.get("tool_calls").is_some() {
        "tool_calls"
    } else {
        "stop"
    };
    Json(
        json!({"id": "chatcmpl-mock", "object": "chat.completion", "model": model,
                "choices": [{"index": 0, "message": message, "finish_reason": finish}],
                "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": 20}}),
    )
    .into_response()
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let port: u16 = std::env::var("MOCK_PORT")
        .ok()
        .and_then(|p| p.parse().ok())
        .unwrap_or(8090);
    let host = std::env::var("MOCK_HOST").unwrap_or_else(|_| "127.0.0.1".into());
    let app = Router::new()
        .route("/v1/chat/completions", post(completions))
        .route("/chat/completions", post(completions))
        .route("/v1/systemone", post(jev::systemone))
        .route("/healthz", get(|| async { "ok" }));
    let listener = tokio::net::TcpListener::bind((host.as_str(), port)).await?;
    eprintln!("mock-llm listening on {host}:{port}");
    axum::serve(listener, app).await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rag_answer_cites_most_relevant_document() {
        let body = json!({"messages": [
            {"role": "system", "content": "<document id=\"S1\" source=\"a\">Laptops are refreshed every 36 months.</document>\n<document id=\"S2\" source=\"b\">The hotel cap is USD 250 per night in tier-1 cities.</document>"},
            {"role": "user", "content": "What is the hotel cap per night?"}]});
        match decide(&body) {
            Reply::Text(t) => assert!(t.contains("USD 250") && t.ends_with("[S2]"), "{t}"),
            Reply::ToolCall { .. } => panic!("expected text"),
        }
    }

    #[test]
    fn scripted_tool_call_only_when_tools_offered() {
        let msg = json!([{"role": "user", "content": "please [[call get_budget {\"department\":\"IT\"}]]"}]);
        match decide(&json!({"messages": msg, "tools": [{"type": "function"}]})) {
            Reply::ToolCall { name, arguments } => assert_eq!(
                (name.as_str(), arguments.as_str()),
                ("get_budget", "{\"department\":\"IT\"}")
            ),
            Reply::Text(_) => panic!("expected tool call"),
        }
        assert!(matches!(decide(&json!({"messages": msg})), Reply::Text(t) if t == "NO_ANSWER"));
    }

    #[test]
    fn tool_result_turn_gets_final_answer() {
        let body = json!({"messages": [{"role": "user", "content": "x"}, {"role": "tool", "content": "{\"remaining\": 42}"}]});
        assert!(matches!(decide(&body), Reply::Text(t) if t.contains("42")));
    }
}
