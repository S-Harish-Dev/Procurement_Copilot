# ADR-03: TypeSafe Jev for routing and the relevance/injection gate, with deterministic degradation

- Status: accepted, quality numbers pending an API key (2026-10-30)

## Decision
Two Jev calls per Q&A request, both on PII-masked text:
- Router (one request): `intent` (choice), `complexity` (choice → LLM tier), `prompt_injection` (noul).
- Gate (one *batched* request): `rel_i` + `inj_i` nouls for each of the 20 RRF candidates; keep
  `rel ≥ τ_rel`, drop `inj ≥ τ_inj`, best first, cap 8, abstain on 0. If every relevant passage was dropped as
  injected, the request is treated as a silent block (not a plain no-match).

Question sets are versioned files (`prompts/router_questions_v1.json`, `gate_questions_v1.json`); types follow
the published OpenAPI (v0.2.0), including `noul.criteria {true,false}`.

## Failure handling
Every failure (timeout at 800 ms, 429/529/5xx, transport error, open breaker, budget ≥ 95%) is one error
(`Unavailable`) and triggers degraded mode: expensive tier, keyword intent, no gate (RRF top-6 minus
ingestion-flagged chunks), regex (+ Prompt Guard 2 in M6) for injection. A 4xx validation error is a bug and
does *not* open the breaker. Spend is metered in micro-dollars and persisted to Valkey.

## Alternatives
- *Cross-encoder reranker (local)*: no injection signal, and CPU latency for 20 pairs on a laptop.
- *LLM-as-reranker*: slow, costly, and uses the scarce free-tier LLM quota.

## Open (needs key)
`jev-1.14.0` pin availability; real latency; the batched-vs-per-passage spike (ADR-07); τ tuning (M10).
Offline runs use a clearly labelled heuristic mock (`mock-llm`'s `/v1/systemone`).
