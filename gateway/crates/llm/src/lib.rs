//! OpenAI-compatible LLM routing with fallback.
//!
//! Every provider (Groq, Gemini, OpenRouter, the mock) speaks the OpenAI chat-completions API, so
//! one client serves all of them. Request bodies are passed through as raw JSON — the router only
//! sets `model` and `stream` — which lets the brain's tool calls flow through the internal proxy.
//!
//! Fallback rule: a provider may be abandoned only **before its first streamed event**. After
//! tokens have reached the user, switching providers would splice two different answers.

pub mod config;
pub mod quota;
pub mod sse;

use std::pin::Pin;
use std::sync::Arc;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::time::{Duration, Instant};

use futures::{Stream, StreamExt};
use metrics::counter;
use rand::RngExt;
use serde_json::Value;
use telemetry::names;
use tracing::{Instrument, info_span, warn};

pub use config::{LlmConfig, ProviderConfig};
use quota::Quota;
use resilience::Breaker;
use sse::{SseDecoder, SseEvent};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Tier {
    Cheap,
    Expensive,
}

impl Tier {
    pub fn parse(s: &str) -> Option<Tier> {
        match s {
            "cheap" => Some(Tier::Cheap),
            "expensive" => Some(Tier::Expensive),
            _ => None,
        }
    }
    pub fn as_str(self) -> &'static str {
        match self {
            Tier::Cheap => "cheap",
            Tier::Expensive => "expensive",
        }
    }
}

#[derive(Debug, thiserror::Error)]
pub enum LlmError {
    #[error("rate limited by {0}")]
    RateLimited(String),
    #[error("upstream error from {provider}: status {status}")]
    Upstream { provider: String, status: u16 },
    #[error("authentication rejected by {0}")]
    Auth(String),
    #[error("request rejected by {provider}: {detail}")]
    BadRequest { provider: String, detail: String },
    #[error("timeout from {0}")]
    Timeout(String),
    #[error("transport error from {provider}: {detail}")]
    Transport { provider: String, detail: String },
    #[error("all LLM providers unavailable")]
    AllUnavailable,
}

impl LlmError {
    /// Worth retrying on the *same* provider?
    fn retryable(&self) -> bool {
        matches!(
            self,
            LlmError::RateLimited(_)
                | LlmError::Upstream { .. }
                | LlmError::Timeout(_)
                | LlmError::Transport { .. }
        )
    }

    /// Short label for `llm_fallback_total{reason}`.
    fn reason(&self) -> &'static str {
        match self {
            LlmError::RateLimited(_) => "rate_limited",
            LlmError::Upstream { .. } => "upstream_5xx",
            LlmError::Auth(_) => "auth",
            LlmError::BadRequest { .. } => "bad_request",
            LlmError::Timeout(_) => "timeout",
            LlmError::Transport { .. } => "transport",
            LlmError::AllUnavailable => "all_unavailable",
        }
    }
}

/// Which provider/model actually served a call.
#[derive(Clone, Debug)]
pub struct Served {
    pub provider: String,
    pub model: String,
    pub tier: Tier,
    pub fallbacks: u32,
}

#[derive(Debug)]
pub enum StreamEvent {
    /// One raw OpenAI `chat.completion.chunk` object.
    Chunk(Value),
    /// End of stream with token accounting (exact when the provider reports usage, else estimated).
    End {
        input_tokens: u64,
        output_tokens: u64,
        estimated: bool,
    },
}

pub type ChatStream = Pin<Box<dyn Stream<Item = Result<StreamEvent, LlmError>> + Send>>;

struct Provider {
    cfg: ProviderConfig,
    /// One or more API keys (comma-separated in the env var), used round-robin.
    keys: Vec<String>,
    next_key: AtomicUsize,
    breaker: Breaker,
    quota: Quota,
}

impl Provider {
    fn model(&self, tier: Tier) -> &str {
        match tier {
            Tier::Cheap => &self.cfg.models.cheap,
            Tier::Expensive => &self.cfg.models.expensive,
        }
    }

    fn classify(&self, status: reqwest::StatusCode, body: String) -> LlmError {
        let provider = self.cfg.name.clone();
        match status.as_u16() {
            429 => LlmError::RateLimited(provider),
            401 | 403 => LlmError::Auth(provider),
            s if s >= 500 => LlmError::Upstream {
                provider,
                status: s,
            },
            _ => LlmError::BadRequest {
                provider,
                detail: body.chars().take(300).collect(),
            },
        }
    }

    fn transport(&self, e: &reqwest::Error) -> LlmError {
        if e.is_timeout() {
            LlmError::Timeout(self.cfg.name.clone())
        } else {
            LlmError::Transport {
                provider: self.cfg.name.clone(),
                detail: e.to_string(),
            }
        }
    }
}

/// Splits a comma-separated key list, dropping blanks.
fn parse_keys(raw: &str) -> Vec<String> {
    raw.split(',')
        .map(str::trim)
        .filter(|k| !k.is_empty())
        .map(str::to_owned)
        .collect()
}

#[cfg(test)]
mod key_tests {
    use super::parse_keys;

    #[test]
    fn splits_trims_and_drops_blanks() {
        assert_eq!(parse_keys(" a, b ,,c,"), ["a", "b", "c"]);
        assert_eq!(parse_keys("solo"), ["solo"]);
        assert!(parse_keys(" , ").is_empty());
    }
}

/// Shared, cheap to clone.
#[derive(Clone)]
pub struct Router {
    inner: Arc<RouterInner>,
}

struct RouterInner {
    http: reqwest::Client,
    providers: Vec<Provider>,
    retries: u32,
}

impl Router {
    /// Providers whose API-key env var is unset or empty are skipped (and logged).
    pub fn new(cfg: LlmConfig, env: impl Fn(&str) -> Option<String>) -> Self {
        let providers = cfg
            .providers
            .into_iter()
            .filter_map(|p| match parse_keys(&env(&p.api_key_env).unwrap_or_default()) {
                keys if !keys.is_empty() => Some(Provider {
                    breaker: Breaker::new(cfg.breaker.failure_threshold, Duration::from_secs(cfg.breaker.cooldown_secs)),
                    quota: Quota::new(p.limits.clone()),
                    keys,
                    next_key: AtomicUsize::new(0),
                    cfg: p,
                }),
                _ => {
                    warn!(provider = %p.name, env = %p.api_key_env, "no API key: provider disabled");
                    None
                }
            })
            .collect();
        let http = reqwest::Client::builder()
            .connect_timeout(Duration::from_secs(5))
            .pool_idle_timeout(Duration::from_secs(90))
            .build()
            .expect("reqwest client");
        Self {
            inner: Arc::new(RouterInner {
                http,
                providers,
                retries: cfg.retries,
            }),
        }
    }

    pub fn provider_names(&self) -> Vec<String> {
        self.inner
            .providers
            .iter()
            .map(|p| p.cfg.name.clone())
            .collect()
    }

    /// Streaming chat with fallback. `body` is an OpenAI request without `model`/`stream`.
    pub async fn chat_stream(
        &self,
        tier: Tier,
        body: Value,
    ) -> Result<(Served, ChatStream), LlmError> {
        let mut fallbacks = 0;
        let mut previous: Option<(&str, &'static str)> = None;
        for (idx, p) in self.inner.providers.iter().enumerate() {
            if !p.breaker.allow() {
                note_skip(p, "breaker_open", &mut previous);
                continue;
            }
            if !p.quota.try_acquire() {
                note_skip(p, "quota", &mut previous);
                continue;
            }
            for attempt in 0..=self.inner.retries {
                match self.open_stream(p, tier, &body).await {
                    Ok(stream) => {
                        p.breaker.on_success();
                        if let Some((from, reason)) = previous {
                            counter!(names::LLM_FALLBACK, "from" => from.to_owned(), "to" => p.cfg.name.clone(), "reason" => reason).increment(1);
                        }
                        let served = Served {
                            provider: p.cfg.name.clone(),
                            model: p.model(tier).to_owned(),
                            tier,
                            fallbacks,
                        };
                        counter!(names::LLM_REQUESTS, "provider" => served.provider.clone(), "model" => served.model.clone(), "result" => "ok").increment(1);
                        let accounted = self.account(idx, served.clone(), stream);
                        return Ok((served, accounted));
                    }
                    Err(e) => {
                        counter!(names::LLM_REQUESTS, "provider" => p.cfg.name.clone(), "model" => p.model(tier).to_owned(), "result" => e.reason()).increment(1);
                        warn!(provider = %p.cfg.name, attempt, error = %e, "LLM call failed");
                        match &e {
                            LlmError::Auth(_) => p.breaker.trip(),
                            LlmError::RateLimited(_) => {
                                p.quota.exhaust_minute();
                                p.breaker.on_failure();
                            }
                            _ => p.breaker.on_failure(),
                        }
                        let retry = e.retryable()
                            && attempt < self.inner.retries
                            && !matches!(e, LlmError::RateLimited(_));
                        previous = Some((p.cfg.name.as_str(), e.reason()));
                        if !retry {
                            break;
                        }
                        tokio::time::sleep(backoff(attempt)).await;
                    }
                }
            }
            fallbacks += 1;
        }
        Err(LlmError::AllUnavailable)
    }

    /// Non-streaming chat with the same fallback rules (used by the internal proxy when the
    /// brain asks for `stream: false`).
    pub async fn chat(&self, tier: Tier, body: Value) -> Result<(Served, Value), LlmError> {
        let (served, mut stream) = self.chat_stream(tier, body).await?;
        let mut acc = CompletionAccumulator::default();
        while let Some(ev) = stream.next().await {
            if let StreamEvent::Chunk(c) = ev? {
                acc.push(&c);
            }
        }
        Ok((served.clone(), acc.finish(&served.model)))
    }

    /// Round-robins the provider's keys; a key that is rate-limited or rejected falls through to
    /// the next one, so the provider only fails once every key has.
    async fn open_stream(
        &self,
        p: &Provider,
        tier: Tier,
        body: &Value,
    ) -> Result<ChatStream, LlmError> {
        let n = p.keys.len();
        let start = p.next_key.fetch_add(1, Ordering::Relaxed);
        let mut last = None;
        for i in 0..n {
            match self
                .open_stream_with_key(p, &p.keys[(start + i) % n], tier, body)
                .await
            {
                Err(e @ (LlmError::RateLimited(_) | LlmError::Auth(_))) if i + 1 < n => {
                    warn!(provider = %p.cfg.name, key_index = (start + i) % n, error = %e, "key failed, trying next");
                    last = Some(e);
                }
                r => return r,
            }
        }
        Err(last.unwrap_or(LlmError::AllUnavailable))
    }

    async fn open_stream_with_key(
        &self,
        p: &Provider,
        key: &str,
        tier: Tier,
        body: &Value,
    ) -> Result<ChatStream, LlmError> {
        let mut body = body.clone();
        body["model"] = Value::String(p.model(tier).to_owned());
        body["stream"] = Value::Bool(true);
        body["stream_options"] = serde_json::json!({"include_usage": true});

        let url = format!("{}/chat/completions", p.cfg.base_url.trim_end_matches('/'));
        let span = info_span!("llm.chat", gen_ai.system = %p.cfg.name, gen_ai.request.model = %p.model(tier), tier = tier.as_str());
        let resp = self
            .inner
            .http
            .post(url)
            .bearer_auth(key)
            .timeout(Duration::from_millis(p.cfg.timeout_ms))
            .json(&body)
            .send()
            .instrument(span)
            .await
            .map_err(|e| p.transport(&e))?;
        if !resp.status().is_success() {
            let status = resp.status();
            let text = resp.text().await.unwrap_or_default();
            return Err(p.classify(status, text));
        }

        // Wait for the first event before committing to this provider.
        let mut bytes = resp.bytes_stream();
        let mut decoder = SseDecoder::default();
        let ttft = Duration::from_millis(p.cfg.first_token_timeout_ms);
        let first: Vec<SseEvent> = tokio::time::timeout(ttft, async {
            loop {
                match bytes.next().await {
                    Some(Ok(chunk)) => {
                        let evs = decoder.push(&chunk);
                        if !evs.is_empty() {
                            return Ok(evs);
                        }
                    }
                    Some(Err(e)) => return Err(p.transport(&e)),
                    None => return Ok(Vec::new()),
                }
            }
        })
        .await
        .map_err(|_| LlmError::Timeout(p.cfg.name.clone()))??;

        let name = p.cfg.name.clone();
        let stream = async_stream_events(first, decoder, bytes, name);
        Ok(stream)
    }

    /// Wraps a provider stream: counts tokens, records cost metrics, emits a final `End`.
    fn account(&self, idx: usize, served: Served, inner: ChatStream) -> ChatStream {
        let router = self.inner.clone();
        let started = Instant::now();
        Box::pin(futures::stream::unfold(
            (inner, Some(TokenTally::default()), router, served, started),
            move |(mut inner, tally, router, served, started)| async move {
                let mut tally = tally?;
                match inner.next().await {
                    Some(Ok(StreamEvent::Chunk(c))) => {
                        tally.observe(&c);
                        Some((
                            Ok(StreamEvent::Chunk(c)),
                            (inner, Some(tally), router, served, started),
                        ))
                    }
                    Some(Ok(end @ StreamEvent::End { .. })) => {
                        Some((Ok(end), (inner, None, router, served, started)))
                    }
                    Some(Err(e)) => Some((Err(e), (inner, None, router, served, started))),
                    None => {
                        let p = &router.providers[idx];
                        let (input, output, estimated) = tally.totals();
                        record_usage(p, &served, input, output);
                        tracing::info!(
                            provider = %served.provider, model = %served.model,
                            gen_ai.usage.input_tokens = input, gen_ai.usage.output_tokens = output,
                            estimated, elapsed_ms = started.elapsed().as_millis() as u64, "llm call done"
                        );
                        Some((
                            Ok(StreamEvent::End {
                                input_tokens: input,
                                output_tokens: output,
                                estimated,
                            }),
                            (inner, None, router, served, started),
                        ))
                    }
                }
            },
        ))
    }
}

fn note_skip<'a>(
    p: &'a Provider,
    reason: &'static str,
    previous: &mut Option<(&'a str, &'static str)>,
) {
    counter!(names::LLM_REQUESTS, "provider" => p.cfg.name.clone(), "model" => "-", "result" => reason).increment(1);
    *previous = Some((p.cfg.name.as_str(), reason));
}

fn record_usage(p: &Provider, served: &Served, input: u64, output: u64) {
    p.quota.record_tokens(input + output);
    let labels = |dir: &'static str| {
        [
            ("provider", served.provider.clone()),
            ("model", served.model.clone()),
            ("dir", dir.to_owned()),
        ]
    };
    counter!(names::LLM_TOKENS, &labels("in")).increment(input);
    counter!(names::LLM_TOKENS, &labels("out")).increment(output);
    // Costs are stored in micro-dollars because Prometheus counters are integers here.
    let shadow =
        (input as f64 * p.cfg.price_in_per_m + output as f64 * p.cfg.price_out_per_m) / 1e6;
    let actual = if p.cfg.free { 0.0 } else { shadow };
    counter!(names::LLM_COST_USD_MICROS, "kind" => "shadow", "provider" => served.provider.clone())
        .increment((shadow * 1e6) as u64);
    counter!(names::LLM_COST_USD_MICROS, "kind" => "actual", "provider" => served.provider.clone())
        .increment((actual * 1e6) as u64);
}

/// Exponential backoff with full jitter: random in [0, 250ms * 2^attempt].
fn backoff(attempt: u32) -> Duration {
    let cap = 250u64.saturating_mul(1 << attempt.min(5));
    Duration::from_millis(rand::rng().random_range(0..=cap))
}

fn async_stream_events(
    first: Vec<SseEvent>,
    decoder: SseDecoder,
    bytes: impl Stream<Item = Result<bytes::Bytes, reqwest::Error>> + Send + 'static,
    provider: String,
) -> ChatStream {
    let state = (
        std::collections::VecDeque::from(first),
        decoder,
        Box::pin(bytes),
        provider,
        false,
    );
    Box::pin(futures::stream::unfold(
        state,
        |(mut queue, mut decoder, mut bytes, provider, done)| async move {
            loop {
                if let Some(ev) = queue.pop_front() {
                    match ev {
                        SseEvent::Done => return None,
                        SseEvent::Data(d) => match serde_json::from_str::<Value>(&d) {
                            Ok(v) => {
                                return Some((
                                    Ok(StreamEvent::Chunk(v)),
                                    (queue, decoder, bytes, provider, done),
                                ));
                            }
                            Err(_) => continue, // skip malformed keep-alives
                        },
                    }
                }
                if done {
                    return None;
                }
                match bytes.next().await {
                    Some(Ok(chunk)) => queue.extend(decoder.push(&chunk)),
                    Some(Err(e)) => {
                        let err = LlmError::Transport {
                            provider: provider.clone(),
                            detail: e.to_string(),
                        };
                        return Some((Err(err), (queue, decoder, bytes, provider, true)));
                    }
                    None => return None,
                }
            }
        },
    ))
}

/// Counts tokens: provider-reported usage when present, else ~4 characters per token.
#[derive(Default)]
struct TokenTally {
    reported: Option<(u64, u64)>,
    output_chars: u64,
}

impl TokenTally {
    fn observe(&mut self, chunk: &Value) {
        if let Some(u) = chunk.get("usage").filter(|u| !u.is_null()) {
            let get = |k: &str| u.get(k).and_then(Value::as_u64).unwrap_or(0);
            self.reported = Some((get("prompt_tokens"), get("completion_tokens")));
        }
        if let Some(content) = chunk
            .pointer("/choices/0/delta/content")
            .and_then(Value::as_str)
        {
            self.output_chars += content.chars().count() as u64;
        }
    }

    fn totals(&self) -> (u64, u64, bool) {
        match self.reported {
            Some((i, o)) => (i, o, false),
            None => (0, self.output_chars.div_ceil(4), true),
        }
    }
}

/// Rebuilds a non-streaming `chat.completion` object from streamed chunks (text and tool calls).
#[derive(Default)]
pub struct CompletionAccumulator {
    content: String,
    tool_calls: Vec<Value>,
    finish_reason: Option<String>,
    usage: Option<Value>,
    id: Option<String>,
}

impl CompletionAccumulator {
    pub fn push(&mut self, chunk: &Value) {
        if self.id.is_none() {
            self.id = chunk.get("id").and_then(Value::as_str).map(str::to_owned);
        }
        if let Some(u) = chunk.get("usage").filter(|u| !u.is_null()) {
            self.usage = Some(u.clone());
        }
        let Some(choice) = chunk.pointer("/choices/0") else {
            return;
        };
        if let Some(r) = choice.get("finish_reason").and_then(Value::as_str) {
            self.finish_reason = Some(r.to_owned());
        }
        let Some(delta) = choice.get("delta") else {
            return;
        };
        if let Some(c) = delta.get("content").and_then(Value::as_str) {
            self.content.push_str(c);
        }
        if let Some(calls) = delta.get("tool_calls").and_then(Value::as_array) {
            for call in calls {
                let index = call.get("index").and_then(Value::as_u64).unwrap_or(0) as usize;
                while self.tool_calls.len() <= index {
                    self.tool_calls.push(serde_json::json!({"type": "function", "function": {"name": "", "arguments": ""}}));
                }
                let slot = &mut self.tool_calls[index];
                if let Some(id) = call.get("id") {
                    slot["id"] = id.clone();
                }
                if let Some(name) = call.pointer("/function/name").and_then(Value::as_str) {
                    let cur = slot["function"]["name"].as_str().unwrap_or("").to_owned();
                    slot["function"]["name"] = Value::String(cur + name);
                }
                if let Some(args) = call.pointer("/function/arguments").and_then(Value::as_str) {
                    let cur = slot["function"]["arguments"]
                        .as_str()
                        .unwrap_or("")
                        .to_owned();
                    slot["function"]["arguments"] = Value::String(cur + args);
                }
            }
        }
    }

    pub fn finish(self, model: &str) -> Value {
        let mut message = serde_json::json!({"role": "assistant", "content": self.content});
        if !self.tool_calls.is_empty() {
            message["tool_calls"] = Value::Array(self.tool_calls);
            if self.content.is_empty() {
                message["content"] = Value::Null;
            }
        }
        serde_json::json!({
            "id": self.id.unwrap_or_else(|| "chatcmpl-gateway".into()),
            "object": "chat.completion",
            "model": model,
            "choices": [{"index": 0, "message": message, "finish_reason": self.finish_reason.unwrap_or_else(|| "stop".into())}],
            "usage": self.usage.unwrap_or(Value::Null),
        })
    }
}

#[cfg(test)]
mod tests;
