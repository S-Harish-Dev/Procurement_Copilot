# ADR-08: Deterministic degradation per dependency (resilience matrix)

- Status: accepted (2026-10-30), drilled in M9

## Decision
Every dependency has one fixed degraded behaviour; nothing degrades "best effort":

| Failure | Behaviour |
|---|---|
| Jev (timeout 800 ms, 429/529/5xx, open breaker, budget ≥ 95%) | Expensive tier, keyword intent, no gate (RRF top-6 minus ingestion-flagged chunks), regex + local classifier guard |
| One LLM provider | Retry with full-jitter backoff, 429 skips ahead, then the next provider; never mid-stream |
| All LLM providers | Fixed "temporarily unavailable" message; cached answers still served |
| Valkey | L1 cache only; rate limiting in-process |
| Qdrant / embedder | Q&A "temporarily unavailable"; action path unaffected |
| OPA | Fail closed: retrieval scope public-only for everyone, writes denied |
| Postgres | Actions unavailable with a clear message; Q&A works |
| Brain | Action intents "temporarily unavailable"; Q&A unaffected |
| Classifier (timeout/error/not loaded) | Request marked degraded, counted (`classifier_skipped_total`), alerted, never cached |

Breakers: 3 consecutive failures open for 30 s, then one half-open trial (`crates/resilience`). Degraded answers
are never written to the answer cache, so an outage cannot outlive itself by 24 h. Admins see `degraded` in the
diagnostics; users never see a reason.

## Why
Predictable failure is testable failure: each row is one drill with a pass/fail check. Fail-closed authorization
is the only safe default when policy is unknown. Serving cached answers during an LLM outage keeps the common
questions working at zero model cost.

## Measured (`make chaos`, run 20261030T181632Z, native stack, docs/chaos-drills.md)
8/8 drills pass. Recovery to an *uncached, non-degraded* answer after restart: Jev 17.1 s (the 30 s breaker
cooldown minus elapsed time), others 0.7-2.2 s. The Jev drill confirms the admin-only `degraded` flag and the
`jev_degraded` gauge = 1; the OPA drill confirms Finance falls back to public-only documents.

## Found by drilling (fixed)
- Under load the single classifier session timed out and the request was treated as clean (fail-open). Now a
  4 × 4 pool, and a missing score is degraded, never "clean" (M9).
- `/readyz` reported ready before the classifier loaded; ~200 requests after restarts skipped layer 4. Readiness
  now waits for the classifier load attempt.

## Alternatives rejected
- *Fail open on OPA outage*: availability at the cost of confidentiality; unacceptable for restricted documents.
- *Queue requests until a dependency returns*: hides outages and builds unbounded latency.
