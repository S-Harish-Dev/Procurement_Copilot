# 6. Glossary

**Abstention / no-answer template.** The single fixed message returned when there is no answer, the user may not see the
matching documents, the question is out of scope, or the request was blocked. Identical in all cases (no existence oracle).

**ACL filter.** The Qdrant filter built from OPA `doc_grants` (one `should` clause per classification grant, plus a
`must` on the active `index_version`), applied inside both the dense and BM25 searches.

**ActionCard.** The UI card for an agent proposal: vendor, line items, total, budget before → after, approval chain,
Confirm/Cancel.

**BM25.** Sparse lexical ranking by term frequency and inverse document frequency. Documents are encoded in Python
(fastembed `Qdrant/bm25`), queries by a Rust reimplementation with proven token-id parity.

**Bootstrap CI.** A 95% confidence interval from resampling eval cases (seeded), reported with every eval metric.

**Canary.** A random per-process token placed in the system prompt; if it ever appears in output the answer is withdrawn
(`replace` event) and counted as an output block.

**Circuit breaker.** Per dependency: 3 consecutive failures open it for 30 s, then one half-open trial (`crates/resilience`).

**Degraded mode.** The fixed fallback behaviour when a dependency fails (ADR-08). Degraded answers are never cached;
admins see `degraded` in the diagnostics.

**`doc_grants`.** OPA output `{classification: [departments] | ["*"]}` describing what a user may retrieve.

**`ef` (`hnsw_ef`).** Size of the candidate list during an HNSW search; higher means better recall and more work. At this
corpus size every ef from 8 to 256 returned 100% of the exact top-20.

**Ensemble verdict.** The injection decision combining regex, Jev and the classifier (`injection_verdict`): strong
signals block alone, mid-confidence classifier scores need a second signal, short messages always do.

**Guard layer 4.** The local ONNX injection classifier (ProtectAI DeBERTa-v3 stand-in for Prompt Guard 2).

**HNSW.** Hierarchical Navigable Small World: a multi-layer proximity graph searched greedily from sparse top layers to the
dense bottom layer; Qdrant's vector index (m = 16).

**Hybrid retrieval.** Dense top-40 + BM25 top-40, fused with RRF to the top 20.

**Index bundle.** The checksummed output of the build machine (`data/index_bundle`): documents, chunks, sparse vectors and
four dense matrices, loaded on the laptop without model inference.

**`index_version`.** Integer on every chunk and in every cache key; flipping the active version in Postgres switches the
index and invalidates all caches.

**Jev.** TypeSafe's hosted decision model, used for the router (intent, complexity, injection) and the gate (per-passage
relevance and injection). Mocked offline.

**`key_terms`.** Sorted tokens containing a digit (amounts, ids, clause numbers, PII placeholders); the semantic cache
requires an exact match on them.

**Matryoshka embeddings.** Embeddings trained so a prefix of the vector is itself a usable embedding: nomic at 256 dims kept
recall@20 0.872 vs 0.895 at 768.

**MCP (Model Context Protocol).** The protocol between the agent and its tool servers (`acme-procurement`, `acme-notify`),
here over stateless Streamable HTTP.

**Mock LLM / mock Jev.** Offline stand-ins (`gateway/crates/mock-llm`): an extractive OpenAI-compatible server and a
heuristic Jev (keyword intent, word-overlap relevance, regex injection). Their numbers are pipeline numbers only.

**Noul.** A Jev question type returning a probability that a statement is true (e.g. "this passage is relevant").

**OBO token (on-behalf-of).** A 60-second JWT (aud `mcp`) the gateway mints for one agent run so MCP servers act as the
user and re-check OPA.

**OPA.** Open Policy Agent; evaluates `policies/authz.rego` with data in `policies/data.json`.

**Proposal.** The stored, validated but unexecuted form of a write action; only a human confirmation turns it into a
one-time HMAC token.

**Recall@20 / hit@5 / MRR / nDCG@10.** Share of gold chunks in the top 20; whether any gold chunk is in the top 5;
mean reciprocal rank of the first gold chunk; rank-discounted gain over the top 10.

**Red team.** `make redteam`: 144 attack cases in 8 categories plus 150 benign questions, with hard gates at zero.

**Rescoring.** Re-ranking int8 candidates with the full float32 vectors (stored on disk).

**RRF (Reciprocal Rank Fusion).** `score = Σ w / (k + rank)` over branches; here k = 2, equal weights, fused in the
gateway with a deterministic tie-break (best single-branch rank, then chunk id).

**Scalar (int8) quantization.** Each vector dimension stored as one byte in RAM (¼ of float32); measured equal recall with
rescoring.

**`scope_hash`.** `sha256(role | canonical grants)`: the permission scope in every answer-bearing cache key.

**Semantic cache.** Qdrant collection `semcache`: a paraphrase above cosine 0.90 with identical scope, index, prompt,
tier and key terms reuses a stored answer.

**Shadow cost.** What the answers would have cost at the expensive tier with no cache and no router; compared with actual
cost on the Insights overview.

**Silent failure.** A blocked request receives exactly the no-answer template, the same SSE events and a delay drawn from
real answer latency, so an attacker can't tell it was blocked.

**Spotlighting.** Retrieved documents are wrapped in `<document id="S1" …>` tags and labelled as data the model must cite,
never follow.

**Strikes.** Per-user counter of blocked attempts in Valkey: throttle at 3, lock at 10 (24 h).

**τ (tau).** A decision threshold: `τ_inj` (Jev injection, 0.8), `τ_cls` (classifier alone, 0.9), `τ_cls_vote` (0.5),
`τ_rel` (gate relevance, 0.5), semantic-cache cosine (0.90).

**Trace ring.** The gateway's in-memory list of the last 1,000 chat requests (stage spans, diagnostics, no text) behind
`/admin/traces`.
