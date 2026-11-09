# 2. Components in depth

One section per design decision. Each covers what it is, why it was chosen, how it works (with
file paths), the knobs (environment variables and their defaults, from `gateway/crates/api/src/config.rs`
unless stated), failure modes, and the metrics that show it working. Numbers are from our own runs
(offline: mock LLMs and mock Jev, real everything else) and are cited to the file that holds them.

---

## 2.1 Rust gateway + Python brain (ADR-01)

What. `gateway/` is a Rust 1.99 workspace (edition 2024, `unsafe_code` forbidden, clippy `-D warnings`) with
crates `api`, `auth`, `policy`, `guard`, `classifier`, `retrieval`, `cache`, `jev`, `llm`, `resilience`,
`telemetry`, `mock-llm`. `brain/` is Python 3.14 with the agent (`brain/agent`), MCP servers
(`brain/mcp_servers`), ingestion and evals.

Why. The hot path (auth, guards, cache, retrieval, streaming) is concurrency- and latency-bound and needs a
single choke point for every model call (cost, fallback, tracing). The agent/MCP/NLP ecosystem is Python-first.

How. gRPC contract `proto/brain.proto`: `RunAgent` (stream), `ExecuteConfirmed` (stream), `CallTool`. The
brain reaches models only through the gateway's internal OpenAI-compatible endpoint
(`GATEWAY_INTERNAL_BIND`, default `127.0.0.1:8081`, shared `INTERNAL_API_TOKEN` ≥ 32 chars).

| Knob | Default | Meaning |
|---|---|---|
| `GATEWAY_BIND` | `0.0.0.0:8080` | Public API (native stack binds 127.0.0.1) |
| `GATEWAY_INTERNAL_BIND` | `127.0.0.1:8081` | LLM proxy for the brain, never public |
| `GATEWAY_METRICS_BIND` | `0.0.0.0:9464` | Prometheus scrape endpoint |
| `BRAIN_URL` | `http://localhost:50051` | gRPC brain |
| `STAGE_TIMEOUT_MS` | 3000 | Per-stage timeout (embed, Qdrant) |

Failure modes. Brain down → action intents get "temporarily unavailable", Q&A unaffected (chaos drill pass).
Two toolchains; embedding parity between Rust (queries) and Python (documents) must be tested (cosine ≥ 0.999).

---

## 2.2 LLM layer: free-tier providers, fallback, breakers, quotas (ADR-02)

What. `gateway/crates/llm`: one OpenAI-compatible streaming client over providers listed in fallback order in
`gateway/providers.toml` (groq → gemini → openrouter; offline `providers.mock.toml`: `mock-primary` →
`mock-secondary`). Each provider has two tiers (`cheap`, `expensive`), a circuit breaker and a quota tracker.

Rules.
1. Fallback only before the first streamed event: switching mid-answer would splice two answers. A stall
   past `first_token_timeout_ms` (6 s groq, 8 s gemini, 10 s openrouter) counts as failure.
2. Retryable errors (5xx, timeout, transport) retry up to `retries = 2` with full-jitter backoff; **429 skips to
   the next provider** and marks the minute exhausted.
3. 401/403 trips the breaker at once; 3 consecutive failures open it for 30 s, then one half-open trial.
4. RPM/RPD/TPM tracked in-process (e.g. groq `rpm=30, rpd=1000, tpm=8000`) to route around a provider before it 429s.
5. Every call records tokens and shadow (paid list price) vs actual (0 on free tiers) cost.

Failure modes. Primary down → served by the fallback (`llm_fallback_total` +1, provider `mock-secondary`);
all down → "The assistant is temporarily unavailable…" while cached answers keep serving (both chaos drills pass).
Quotas are per gateway instance (move to Valkey to scale out).

Metrics. `llm_requests_total{provider,model,result}`, `llm_fallback_total{from,to,reason}`,
`llm_tokens_total{provider,model,dir}`, `llm_cost_usd_micros_total{kind=actual|shadow,provider}` (integer
micro-dollars; dashboards divide by 1e6).

---

## 2.3 Jev router (ADR-03)

What. One TypeSafe Jev request per chat message, on masked text, with the versioned question set
`prompts/router_questions_v1.json`: `intent` (policy_qa, data_lookup, create_request, approve, out_of_scope),
complexity → tier, and a `prompt_injection` noul.

How. `JevClient::route` (`gateway/crates/jev`). Decision rules (`decide_route` in `chat.rs`): intent
confidence < `MIN_INTENT_CONFIDENCE` (0.5) → `policy_qa`; `p_expensive ≥ TAU_ROUTE` (0.5) → expensive tier.
Action intents go to the brain; `out_of_scope` gets the no-answer template. Route decisions are cached per
masked text + recent turns (24 h).

| Knob | Default |
|---|---|
| `TYPESAFE_API_KEY`, `JEV_BASE_URL`, `JEV_MODEL` | empty, `https://api.typesafe.ai`, `jev-latest` |
| `JEV_TIMEOUT_MS` | 800 |
| `JEV_PRICE_IN_PER_M` | 0.042 (USD per 1M input tokens) |
| `JEV_BUDGET_USD` | 5.0 (warn at 80%, degrade at 95%) |
| `TAU_INJ` | 0.8 |

Honest status. Offline, Jev is a labelled heuristic mock (`mock-llm`'s `/v1/systemone`: keyword intent,
word-overlap relevance, regex injection), served by its own `mock-jev` instance (:8092) so a Jev outage can be
drilled independently. Real latency, quality and the router cost-vs-quality curve need a key.

Metrics. `jev_requests_total{kind,result}`, `jev_cost_usd_micros_total`, `jev_degraded`,
`route_decisions_total{intent,tier}`, `jev_budget_spent_usd`.

---

## 2.4 Vector database: Qdrant with HNSW, and why not FAISS (Limitation.md §6)

What. Qdrant 1.20, collection `chunks`: named dense vector `dense` (768-d, cosine, HNSW `m = 16`,
`ef_construct = 128`, scalar int8 quantization `always_ram`, originals on disk, rescoring on) plus sparse
vector `bm25` (IDF modifier). Payload: `chunk_id, doc_id, source, title, section_path, department,
classification, index_version, text (masked), injection_flag`, keyword payload indexes on the filter fields.
Created by `brain/ingestion/store.py`. Quarantined chunks never reach Qdrant.

Why HNSW in a database rather than FAISS. HNSW is an algorithm; FAISS is a library that implements it (and
IVF, PQ, flat). The deciding requirement is access control inside the search: filtering after the search can
return fewer than top-k, and naive pre-filtering on HNSW collapses recall. Qdrant's filter-aware HNSW plus its
planner (small filtered sets → exact search) handles both, and it has native sparse vectors for BM25,
persistence, payloads and snapshots. FAISS would leave filtering, persistence and sparse search to us.

| At scale | Best choice |
|---|---|
| ≤ ~100M vectors in RAM, low latency, high recall | HNSW (+ int8 + rescoring, our setup) |
| 100M+ and memory-bound | IVF-PQ (FAISS/Milvus) or DiskANN |
| GPU batch search, offline dedup | FAISS GPU |
| Heavy permission filters, updates, hybrid | A filter-aware database (Qdrant) |

Measured (M10, `evals/experiments/hnsw_ef.json`, `quantization.json`). `hnsw_ef` 8…256 all return 100% of
the exact top-20 and identical recall@20 0.895: ACL-filtered sets are small, so the search is effectively
exact. int8 + rescore = int8 without rescore = fp32 = brute force (recall@20 0.895). At 11,322 chunks memory is
not the constraint; at 100× it is, and int8 (4× less RAM) is already on.

---

## 2.5 Embeddings, BM25 and hybrid retrieval with gateway-side RRF (ADR-05)

What. Documents embedded once in Python (fastembed, `search_document: ` prefix, contextual header
"title › section path"); queries embedded in Rust (fastembed-rs, `search_query: `), L2-normalized (fastembed
returns raw nomic vectors). BM25: fastembed `Qdrant/bm25` for documents, reimplemented in Rust for queries
(`retrieval/src/bm25.rs`), with parity fixtures from Python (identical token-id sets on 12 queries).

Corpus. 177 documents → 11,322 chunks, 2.85M tokens (FAR 5,917 · CUAD 3,609 · Acme 830 · UN 591 ·
World Bank 375). Chunking (`brain/ingestion/chunk.py`): structure-aware, `TARGET_TOKENS = 400`,
`OVERLAP_TOKENS = 60` (15%). Injection scan quarantined exactly the 5 planted chunks; 0 of 172 benign documents flagged.

Hybrid query. One Qdrant batch call: dense top-40 + BM25 top-40, the same ACL filter on both branches
(`acl_filter`: one `should` clause per classification grant, `must` active `index_version`). Fusion in the gateway:

```
RRF(d) = Σ over branches b ∈ {dense, bm25} of  1 / (k + rank_b(d))      k = 2, ranks start at 1
```

Ties break by best single-branch rank, then chunk id (`retrieval::search::rrf`). Fusion moved out of Qdrant in
M6 because the server returned equal-score ties (dense rank *i* = BM25 rank *i*) in arbitrary order: 24 of 30
repeated queries changed their top-20, and eval MRR swung ±0.02 between identical runs.

| Knob | Default |
|---|---|
| `EMBEDDER_INSTANCES` × `EMBEDDER_THREADS` | 4 × 4 (fastembed's default of *all cores per instance* oversubscribed the CPU) |
| dense_k / sparse_k / top_k | 40 / 40 / 20 (`HybridParams`) |
| `QDRANT_GRPC_URL` | `http://localhost:6334` |

Measured. Baseline v3 (eval-fast, 185 answerable cases): recall@20 0.976, hit@5 0.913, MRR 0.730,
nDCG@10 0.771. M10 (`fusion.json`): dense only MRR 0.679, BM25 only 0.619, RRF k=2 0.730 (best), k=10 0.721,
k=60 0.701, weighted 0.7/0.3 0.700. Embedders (`embedders.json`, dense only): nomic-v1.5 MRR 0.679 >
nomic-Q 0.671 > bge-small 0.635 > bge-base 0.562. Matryoshka (`matryoshka.json`): 256 dims keep recall@20 0.872
vs 0.895. Latency after the M9 thread fix: single request p50 72-87 ms; at 100 VUs CPU-bound (~53-73 RPS,
p95 ~2 s, target missed; see Limitation.md §12).

Metrics. `stage_duration_seconds{stage="embed"|"retrieve.hybrid"}`.

---

## 2.6 Jev gate: relevance and injection per passage (ADR-03, ADR-07)

What. One batched Jev request: `rel_i` and `inj_i` for each of the 20 fused candidates
(`prompts/gate_questions_v1.json`). `jev::select` keeps `rel ≥ TAU_REL` (0.5), drops `inj ≥ TAU_GATE_INJ` (0.8),
best first, cap `GATE_CAP` (8). If *every* relevant passage was dropped as injected, the request is a silent
block (layer `documents`), not a plain no-match. Gate scores are cached per (query, chunk, index, question
version), all-or-nothing, because mixing cached and fresh scores would change the batch Jev sees.

Open. ADR-07: batched vs per-passage spike (κ, recall, latency, cost) needs a key; rule: keep batched
unless quality drops by more than 2 points.

Metrics. `gate_kept_chunks` (histogram 0-8), `gate_relevance` (0.1 buckets).

---

## 2.7 Degradation

| Failure | Behaviour | Drill result (`docs/chaos-drills.md`) |
|---|---|---|
| Jev down / slow / budget ≥ 95% | Breaker (3 failures → 30 s); keyword intent, expensive tier, no gate (RRF top-6 minus flagged chunks); not cached | pass, recovery 17.1 s |
| Primary LLM down | Next provider | pass |
| All LLMs down | "Temporarily unavailable"; cache still serves | pass |
| Valkey down | L1 only; in-process rate-limit buckets | pass |
| Qdrant down | Q&A "temporarily unavailable"; actions work | pass |
| OPA down | Fail closed: public-only scope, writes denied | pass |
| Postgres down | Actions fail with a clear message; Q&A works | pass |
| Brain down | Actions "temporarily unavailable"; Q&A works | pass |
| Classifier timeout/missing | Degraded: counted, alerted, never cached | (load test: 0 skips after the readiness fix) |

`DEGRADED_TOP_K` = 6. `/readyz` waits for the embedder, Qdrant and the classifier's load attempt.

---

## 2.8 Identity and authorization: JWT + OPA (ADR-09)

What. Three token kinds (`gateway/crates/auth`), each with its own audience so one can't be replayed as
another: user JWT (RS256, `aud=gateway`, `USER_TOKEN_TTL_SECS` 8 h), OBO JWT (RS256, `aud=mcp`,
60 s, minted per agent run), and one-time HMAC confirmation tokens (`CONFIRMATION_HMAC_KEY`). Demo login
(`DEMO_LOGIN=true`) issues user tokens for seeded personas.

OPA. `policies/authz.rego` + `policies/data.json`, one query returns `allow`, `reasons`, `doc_grants`,
`approval_chain`, `requires_confirmation`. Retrieval scope is per-classification grants, e.g. an IT employee
gets `{"public": ["*"], "internal": ["IT", "all"]}`; admins get everything; confidential by role/department list.
`scope_hash = sha256(role | canonical grants)` partitions every cache. Approval chain: manager if > $1k, + finance
if > $10k, + admin if > $100k; personal limits bind only at the final level; Finance may act at the manager level;
`UNIQUE(pr_id, approver_id)` keeps separation of duties. 52 policy tests.

Failure mode. OPA down → public-only scope (fail closed), writes denied. Gaps: `spend_limit` is not
enforced (meaning to confirm); delegation is described in a document but not modelled.

---

## 2.9 Guardrails (ADR-06)

Input layers, in order (`gateway/crates/guard`, `crates/classifier`, `chat.rs::injection_verdict`):

| Layer | What | Blocks when |
|---|---|---|
| Normalize | NFKC, strip zero-width/bidi/control, Cyrillic/Greek look-alikes → Latin, collapse spaces, cap 4,000 chars | (preprocessing) |
| Regex | `prompts/injection_patterns_v1.json` (shared with ingestion) on normalized text and decoded base64/hex | a `high` pattern matches |
| Jev | router's `p_injection` | ≥ `TAU_INJ` 0.8 |
| Classifier | ProtectAI DeBERTa-v3 injection model (stand-in for Prompt Guard 2), int8 MatMul-only, 512-token windows, stride 128, max over windows | ≥ `TAU_CLS` 0.9 alone, if the message has ≥ `CLS_MIN_WORDS` (6) words |
| Ensemble | classifier ≥ `TAU_CLS_VOTE` 0.5 + a second signal | any regex hit, Jev ≥ 0.5, or (≥ 6 words and Jev unavailable) |

The 6-word rule came from M8: the classifier scored "approve this" 1.000 but "approve it" 0.000 and silently
blocked 9/22 ordinary approval commands. With the rule: 0/22 blocked, red-team detection unchanged (88/101),
benign FP unchanged; short attacks no regex covers now rely on Jev and the output canary check.

Classifier operation. `CLASSIFIER_INSTANCES` × `CLASSIFIER_THREADS` = 4 × 4; it runs on its own task
concurrently with the Jev route; timeout 1.5 s. A missing score is degraded, never "clean"
(`classifier_skipped_total{reason}`, alert `ClassifierSkipped`). An exact answer-cache hit doesn't wait for it.
Quantization lesson: naive dynamic int8 scored every probe ≈ 0.02; only the per-channel MatMul-only copy that
agrees with fp32 on labelled probes is kept (539 MB vs 739 MB).

Silent failure. Blocks return the exact no-answer template, the same SSE events and status, padded to a
random draw from recorded real-answer latency (`LatencyWindow`, last 500 computed answers; cache hits excluded).
Strikes in Valkey (24 h): ≥ 3 → 1.5 s throttle, ≥ 10 → every request gets the template; admins reset via
`POST /admin/users/{id}/reset-strikes` (audited). Each block writes a `security_events` row.

Output guard (`guard/output.rs`, sentence segments as they stream): a canary (random per process,
embedded in the system prompt) in the output → the answer is replaced by the template and counted as a block;
markdown images removed; links to non-allowlisted domains (`LINK_ALLOWLIST`, default
`acquisition.gov,worldbank.org,un.org,acme.example`) reduced to text; citations must be among the provided
S-ids; PII unmasked only for roles allowed by OPA (`view_pii`). Documents are spotlighted: wrapped in
`<document id="S1" …>` tags and labelled as data.

Measured. Red team (`make redteam`, 144 attacks + 150 benign): all hard gates 0 (leaks, PII, prompt leak,
unauthorized actions, template mismatch, HTTP abuse 12/12), direct injection 45/45, canary 3/3, extraction 28/35,
role-play 12/18 blocked, benign FPR 0.7%, 96.6% of blocks within the normal timing band. M10 τ sweep
(`tau_classifier.json`): at τ = 0.9 precision 89.4%, recall 83.2% (classifier alone).

Metrics. `guard_blocks_total{layer}`, `abstentions_total{reason}`, `classifier_skipped_total{reason}`,
`ratelimit_rejections_total`.

---

## 2.10 Caching (ADR-11)

Layers. moka L1 (in-process) → Valkey L2 (shared, JSON, any error = miss) → semantic cache (Qdrant
`semcache`). Six caches in `api/src/caching.rs`, keys in `cache/src/keys.rs`:

| Cache | Key | TTL |
|---|---|---|
| answers | `sha256(normalize(masked query) ‖ scope_hash ‖ index_version ‖ prompt_version ‖ tier)` | 24 h |
| semantic | cosine ≥ `SEMCACHE_THRESHOLD` and exact scope_hash, index_version, prompt_version, tier, key_terms | 24 h |
| retrieval | exact masked query, scope, index, hybrid params | 1 h |
| embeddings | model, exact masked query | 7 d |
| Jev route | masked query + recent turns, router version | 24 h |
| Jev gate | masked query, chunk id, index, gate version | 24 h |

Every field is length-prefixed before hashing (`digest`), so user text can't forge a separator:
`("x|y","z")` and `("x","y|z")` hash differently. `normalize_query` lower-cases, collapses whitespace and
drops trailing `?.!`. `key_terms` = sorted tokens containing a digit (amounts, ids, "tier-1", PII placeholders):
"approve under $1,000" and "$10,000" embed almost identically but must never share an answer. L1 TTL ≤ 1 h.

Rules. Masked text is cached and unmasked per request with the requester's own rights. Never cached:
blocks, abstentions, actions, anything degraded. Reads are allowed in degraded mode except with a fail-closed
OPA scope. Embedding and retrieval keys use the exact text so a hit is bit-identical to recomputing.

Invalidation. Index and prompt versions are in every key. The gateway polls the active version every
`INDEX_POLL_SECS` (10 s); on a change it clears L1 and deletes `semcache` points of other versions.
`POST /admin/cache/flush` clears everything (SCAN + UNLINK, audited). `CACHE_ENABLED=false` disables all.

Threshold. M10 sweep (`tau_semcache.json`): paraphrases score 0.765-0.950, the hardest negative among 7,284
same-key-term question pairs scores 0.874. 0.95 (the starting value) hit 0/10; 0.90 hits 3/10 with 0 false
hits (margin 0.026). Live `make cache-check`: 10 isolation probes, 0 cross-scope serves, 30% semantic hits.

Measured. Exact hit 23 ms vs 902 ms computed; load test 4,679 RPS at 64 VUs, p95 25.5 ms
(`docs/loadtest.md`); M9 earlier run 5,930 RPS.

Metrics. `cache_requests_total{cache,layer,result}`, `answers_total{source=llm|cache}`.

---

## 2.11 Agent, MCP servers and confirmed actions (ADR-10)

What. Pydantic AI 2.63 agent per request (`brain/agent`), model = the gateway proxy with the router's tier,
prompt `prompts/agent_v1.md`. Tools over Streamable HTTP from two MCP servers (MCP SDK 2.3 `MCPServer`,
stateless, DNS-rebinding protection): `acme-procurement` (12 tools: search_vendors, get_vendor, get_budget,
get_spend_summary, list_my_requests, get_purchase_request, get_approval_chain, list_pending_approvals,
create/submit_purchase_request, approve/reject_request) and `acme-notify` (template e-mail, chat feed, PO PDF).
Every call verifies the OBO JWT and re-asks OPA; the service layer (`brain/procurement/service.py`) holds the
state machine with row locks.

Confirmation. Writes return a proposal (canonical payload text + sha256, 5-min expiry). Only
`POST /v1/actions/{id}/confirm` mints the one-time HMAC token; `ExecuteConfirmed` replays the stored canonical
text without an LLM; the MCP server re-verifies and marks the token used in the same transaction. Postgres
triggers forbid self-approval and illegal transitions.

Measured. `make agent-tasks`: 37/37, `unauthorized_actions = 0` (offline tasks pass mock tool directives in
a `script` field accepted only with `EVAL_ENDPOINTS=true`, outside the guarded message). Integration tests:
tampered payload, replay, other identity, forged key, double confirm (409), someone else's proposal (404).

Metrics. `actions_total{tool,result}`.

---

## 2.12 Observability

Traces. OpenTelemetry 0.33 spans per stage, W3C trace context into gRPC; exported via
`OTEL_EXPORTER_OTLP_ENDPOINT` to otel-lgtm (Tempo/Loki) in compose. Without Tempo (native stack), the gateway keeps an
in-memory ring of the last 1,000 chat requests with real stage offsets and admin diagnostics (no text):
`GET /admin/traces`, `/admin/traces/{id}` (link to Grafana when `GRAFANA_URL` is set).

Metrics. Pull endpoint `:9464`, latency histograms with explicit buckets (1 ms … 30 s). Known label sets are
pre-registered at zero, because `increase()` can't see a counter's first increment. Prometheus 3.15.1
scrapes it (`infra/prometheus/`); 6 alert rules (guard spike, Jev degraded > 5 min, chat p95 > 8 s, LLM error
rate > 20%, dependency down, classifier skipped).

Dashboards. `make dashboards` generates 5 Grafana dashboards (Overview, LLM & Cost, Retrieval/Gate/Cache,
Security, Dependencies) with one colour per entity; provisioned natively (verified) and mounted into
otel-lgtm in compose (paths unverified until the first `make up`). The admin Insights overview queries the same
Prometheus server-side (`PROMETHEUS_URL`, default `http://127.0.0.1:9090`).

---

## 2.13 Evals

| Suite | Command | What it gates |
|---|---|---|
| eval-fast | `make eval-fast` (~5 s now) | 263 cases through the gateway's real retrieval path; recall@20, hit@5, MRR, nDCG@10 with bootstrap CIs vs `evals/baselines/baseline.json`; guard detection/FPR; hard gates: forbidden-doc retrievals, ACL leaks, OPA test failures |
| agent tasks | `make agent-tasks` | 37 tasks; hard gate unauthorized actions = 0 |
| red team | `make redteam` | 144 attacks / 150 benign; leaks, PII, prompt leak, unauthorized actions, template mismatch, HTTP abuse = 0 |
| cache check | `make cache-check` | cross-scope serves = 0 |
| chaos | `make chaos` | every resilience-matrix row |
| load | `make loadtest` | k6 cache_hit / retrieval / e2e_mock |
| experiments | `make experiments` | M10 ablations and sweeps → Insights |

Regression tolerances are direction-aware (recall@20 −0.02, others −0.03, guard FPR +0.02). Eval-full with real
LLM judges (faithfulness, Jev vs Gemini agreement) needs API keys.

---

## 2.14 Frontend

Next.js 16.4.3 App Router, React 19.3.1, Tailwind 4.3.3, Recharts 3.10.1, TanStack Query 5.105.0; TypeScript
6.0.3 (typescript-eslint does not support 7.0). User pages `/login`, `/chat`, `/requests`, `/requests/[id]`,
`/approvals`, `/budgets`; admin `/insights/{overview,evals,experiments,traces}`. JWT in sessionStorage (bearer
only, nothing for CSRF), SSE parsed from `fetch`, conversations in localStorage. Charts follow one validated
palette with fixed colours per entity, takeaway titles, CSV/JSON/table views. Verified: 183 contract checks
against the live gateway, axe-core 0 WCAG 2.1 AA violations (10 pages × light/dark), Lighthouse `/login`
accessibility 100.
