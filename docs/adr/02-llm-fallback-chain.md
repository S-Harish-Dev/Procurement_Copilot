# ADR-02: Free-tier LLMs behind one OpenAI-compatible router with fallback, breakers and quotas

- Status: accepted (2026-10-30)

## Decision
All generation goes through `gateway/crates/llm`: one OpenAI-compatible client; providers in fallback order
(Groq → Gemini → OpenRouter, `gateway/providers.toml`); each with a circuit breaker and a free-tier quota tracker.

## Rules
1. Fallback only before the first streamed event. After tokens reach the user, switching would splice two
   answers. A stalled stream (no first event within `first_token_timeout_ms`) counts as a failure.
2. Retry the same provider only for retryable errors (5xx, timeout, transport), at most `retries` times, with
   full-jitter exponential backoff; 429 skips straight to the next provider and marks the minute exhausted.
3. 401/403 trips the breaker immediately (a bad key won't fix itself); 3 consecutive failures open it for 30 s,
   then one half-open trial.
4. Quotas (RPM/RPD/TPM) are tracked in-process with fixed windows to route around a provider *before* it 429s.
5. Every call records tokens and shadow cost (paid list price) vs actual cost (0 on free tiers) in
   micro-dollars, plus `llm_fallback_total{from,to,reason}`.

## Alternatives
- *LiteLLM proxy*: capable, but another service (Python) on the hot path and less control over streaming rules.
- *Provider SDKs*: three clients, three error models. All three providers speak the OpenAI API.

## Consequences
Quota tracking is per gateway instance (fine for one replica; move counters to Valkey for horizontal scale).
Free-tier limits are per account/project and must be confirmed in each console; numbers in `providers.toml` are
upper bounds from public docs.
