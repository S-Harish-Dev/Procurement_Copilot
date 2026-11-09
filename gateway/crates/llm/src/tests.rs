use std::time::Duration;

use futures::StreamExt;
use serde_json::json;
use wiremock::matchers::{header, method, path};
use wiremock::{Mock, MockServer, ResponseTemplate};

use super::*;
use crate::config::{BreakerConfig, Limits, TierModels};

fn sse(chunks: &[Value]) -> String {
    let mut s = String::new();
    for c in chunks {
        s.push_str(&format!("data: {c}\n\n"));
    }
    s.push_str("data: [DONE]\n\n");
    s
}

fn text_stream(words: &[&str], usage: Option<(u64, u64)>) -> String {
    let mut chunks: Vec<Value> = words
        .iter()
        .map(|w| json!({"id": "c1", "object": "chat.completion.chunk", "choices": [{"index": 0, "delta": {"content": w}}]}))
        .collect();
    if let Some((i, o)) = usage {
        chunks.push(json!({"id": "c1", "choices": [], "usage": {"prompt_tokens": i, "completion_tokens": o}}));
    }
    sse(&chunks)
}

fn provider(name: &str, uri: &str, limits: Limits) -> ProviderConfig {
    ProviderConfig {
        name: name.into(),
        base_url: uri.into(),
        api_key_env: format!("{}_KEY", name.to_uppercase()),
        models: TierModels {
            cheap: format!("{name}-small"),
            expensive: format!("{name}-large"),
        },
        limits,
        price_in_per_m: 0.5,
        price_out_per_m: 1.0,
        free: true,
        timeout_ms: 2_000,
        first_token_timeout_ms: 300,
    }
}

fn router(providers: Vec<ProviderConfig>) -> Router {
    let cfg = LlmConfig {
        retries: 1,
        breaker: BreakerConfig {
            failure_threshold: 3,
            cooldown_secs: 60,
        },
        providers,
    };
    Router::new(cfg, |_| Some("test-key".into()))
}

async fn collect(stream: ChatStream) -> (String, Option<(u64, u64, bool)>) {
    let events: Vec<_> = stream.collect().await;
    let mut text = String::new();
    let mut end = None;
    for e in events {
        match e.unwrap() {
            StreamEvent::Chunk(c) => {
                if let Some(t) = c
                    .pointer("/choices/0/delta/content")
                    .and_then(Value::as_str)
                {
                    text.push_str(t);
                }
            }
            StreamEvent::End {
                input_tokens,
                output_tokens,
                estimated,
            } => end = Some((input_tokens, output_tokens, estimated)),
        }
    }
    (text, end)
}

fn body() -> Value {
    json!({"messages": [{"role": "user", "content": "hi"}]})
}

#[tokio::test]
async fn streams_from_primary_with_reported_usage() {
    let a = MockServer::start().await;
    Mock::given(method("POST"))
        .and(path("/chat/completions"))
        .and(header("authorization", "Bearer test-key"))
        .respond_with(ResponseTemplate::new(200).set_body_raw(
            text_stream(&["Hel", "lo"], Some((12, 2))),
            "text/event-stream",
        ))
        .mount(&a)
        .await;
    let r = router(vec![provider("groq", &a.uri(), Limits::default())]);
    let (served, stream) = r.chat_stream(Tier::Cheap, body()).await.unwrap();
    assert_eq!(
        (
            served.provider.as_str(),
            served.model.as_str(),
            served.fallbacks
        ),
        ("groq", "groq-small", 0)
    );
    assert_eq!(
        collect(stream).await,
        ("Hello".into(), Some((12, 2, false)))
    );
}

#[tokio::test]
async fn bad_key_falls_back_and_trips_breaker() {
    let a = MockServer::start().await;
    let b = MockServer::start().await;
    // Primary must be called exactly once: after the 401 the breaker is open and it is skipped.
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(401))
        .expect(1)
        .mount(&a)
        .await;
    Mock::given(method("POST"))
        .respond_with(
            ResponseTemplate::new(200)
                .set_body_raw(text_stream(&["ok"], None), "text/event-stream"),
        )
        .expect(2)
        .mount(&b)
        .await;
    let r = router(vec![
        provider("groq", &a.uri(), Limits::default()),
        provider("gemini", &b.uri(), Limits::default()),
    ]);
    for _ in 0..2 {
        let (served, stream) = r.chat_stream(Tier::Expensive, body()).await.unwrap();
        assert_eq!(served.provider, "gemini");
        assert_eq!(collect(stream).await.0, "ok");
    }
}

#[tokio::test]
async fn server_error_is_retried_then_falls_back() {
    let a = MockServer::start().await;
    let b = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(503))
        .expect(2)
        .mount(&a)
        .await; // 1 + 1 retry
    Mock::given(method("POST"))
        .respond_with(
            ResponseTemplate::new(200).set_body_raw(text_stream(&["x"], None), "text/event-stream"),
        )
        .mount(&b)
        .await;
    let r = router(vec![
        provider("groq", &a.uri(), Limits::default()),
        provider("gemini", &b.uri(), Limits::default()),
    ]);
    let (served, _) = r.chat_stream(Tier::Cheap, body()).await.unwrap();
    assert_eq!((served.provider.as_str(), served.fallbacks), ("gemini", 1));
}

#[tokio::test]
async fn all_down_is_an_error_not_a_hang() {
    let a = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(500))
        .mount(&a)
        .await;
    let r = router(vec![
        provider("groq", &a.uri(), Limits::default()),
        provider("dead", "http://127.0.0.1:9", Limits::default()),
    ]);
    assert!(matches!(
        r.chat_stream(Tier::Cheap, body()).await,
        Err(LlmError::AllUnavailable)
    ));
}

#[tokio::test]
async fn stalled_first_token_falls_back_before_user_sees_anything() {
    let a = MockServer::start().await;
    let b = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(
            ResponseTemplate::new(200)
                .set_delay(Duration::from_millis(1_500))
                .set_body_raw(text_stream(&["late"], None), "text/event-stream"),
        )
        .mount(&a)
        .await;
    Mock::given(method("POST"))
        .respond_with(
            ResponseTemplate::new(200)
                .set_body_raw(text_stream(&["fast"], None), "text/event-stream"),
        )
        .mount(&b)
        .await;
    let mut slow = provider("groq", &a.uri(), Limits::default());
    slow.timeout_ms = 1_000;
    let r = router(vec![slow, provider("gemini", &b.uri(), Limits::default())]);
    let (served, stream) = r.chat_stream(Tier::Cheap, body()).await.unwrap();
    assert_eq!(served.provider, "gemini");
    assert_eq!(collect(stream).await.0, "fast");
}

#[tokio::test]
async fn exhausted_quota_routes_around_without_calling_provider() {
    let a = MockServer::start().await;
    let b = MockServer::start().await;
    Mock::given(method("POST"))
        .respond_with(
            ResponseTemplate::new(200).set_body_raw(text_stream(&["a"], None), "text/event-stream"),
        )
        .expect(1)
        .mount(&a)
        .await;
    Mock::given(method("POST"))
        .respond_with(
            ResponseTemplate::new(200).set_body_raw(text_stream(&["b"], None), "text/event-stream"),
        )
        .expect(1)
        .mount(&b)
        .await;
    let r = router(vec![
        provider(
            "openrouter",
            &a.uri(),
            Limits {
                rpm: 1,
                rpd: 0,
                tpm: 0,
            },
        ),
        provider("gemini", &b.uri(), Limits::default()),
    ]);
    assert_eq!(
        r.chat_stream(Tier::Cheap, body()).await.unwrap().0.provider,
        "openrouter"
    );
    assert_eq!(
        r.chat_stream(Tier::Cheap, body()).await.unwrap().0.provider,
        "gemini"
    );
}

#[tokio::test]
async fn non_streaming_reassembles_tool_calls() {
    let a = MockServer::start().await;
    let chunks = vec![
        json!({"id": "c9", "choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [{"index": 0, "id": "call_1", "type": "function", "function": {"name": "get_budget", "arguments": ""}}]}}]}),
        json!({"id": "c9", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\"department\":"}}]}}]}),
        json!({"id": "c9", "choices": [{"index": 0, "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\"IT\"}"}}]}, "finish_reason": "tool_calls"}]}),
    ];
    Mock::given(method("POST"))
        .respond_with(ResponseTemplate::new(200).set_body_raw(sse(&chunks), "text/event-stream"))
        .mount(&a)
        .await;
    let r = router(vec![provider("groq", &a.uri(), Limits::default())]);
    let (_, resp) = r
        .chat(Tier::Cheap, json!({"messages": [], "tools": []}))
        .await
        .unwrap();
    let call = &resp["choices"][0]["message"]["tool_calls"][0];
    assert_eq!(call["id"], "call_1");
    assert_eq!(call["function"]["name"], "get_budget");
    assert_eq!(call["function"]["arguments"], "{\"department\":\"IT\"}");
    assert_eq!(resp["choices"][0]["finish_reason"], "tool_calls");
    assert!(resp["choices"][0]["message"]["content"].is_null());
}

#[test]
fn provider_without_key_is_disabled() {
    let cfg = LlmConfig {
        retries: 0,
        breaker: BreakerConfig::default(),
        providers: vec![provider("groq", "http://x", Limits::default())],
    };
    let r = Router::new(cfg, |_| None);
    assert!(r.provider_names().is_empty());
}

#[test]
fn estimates_tokens_when_usage_missing() {
    let mut t = TokenTally::default();
    t.observe(&json!({"choices": [{"delta": {"content": "abcdefgh"}}]}));
    assert_eq!(t.totals(), (0, 2, true));
}
