# Limitations, constraints and the choices they forced

This file lists every known limitation of Procurement Copilot: the hardware we had, what that ruled out,
what we chose instead, and what would change with more resources. Numbers are measured on
the 64-core build machine on 2026-10-30 unless marked otherwise.

Contents
1. [Hardware and environment](#1-hardware-and-environment)
2. [Build machine vs laptop: the index bundle](#2-build-machine-vs-laptop-the-index-bundle)
3. [Software versions held back](#3-software-versions-held-back)
4. [Data and corpus](#4-data-and-corpus)
5. [Ingestion: parsing, chunking, PII, injection scanning, embedding](#5-ingestion)
6. [Vector index: why HNSW (in Qdrant), and how it compares with FAISS](#6-vector-index-why-hnsw-in-qdrant-and-how-it-compares-with-faiss)
7. [Authorization model: interpretations and gaps](#7-authorization-model-interpretations-and-gaps)
8. [External services and cost](#8-external-services-and-cost)
9. [Open items not yet verified](#9-open-items-not-yet-verified)
10. [Guardrails: the injection classifier and the red team](#10-guardrails-the-injection-classifier-and-the-red-team)
11. [Caching](#11-caching)
12. [UI, observability, load and resilience](#12-ui-observability-load-and-resilience)

---

## 1. Hardware and environment

### 1.1 Build machine

The heavy ingestion ran once on a 64-core, CPU-only machine (no GPU) with 30-48 GB of free RAM. That has three consequences:

- All models run on CPU through ONNX Runtime or PyTorch CPU wheels, so throughput targets may come in lower.
- Every process pool is sized by free memory, not core count (`brain/common/resources.py`: at most 40% of available memory, using measured per-worker memory). The first ingestion run started 32 masking workers plus 16 embedding workers and pushed free memory down to 4 GB of 125 GB before I stopped it.
- Peak memory per worker is about 1.4-2.5 GB for docling, ~1.5 GB for spaCy + Presidio and ~3.4-3.7 GB for nomic embedding. Long jobs run in the background with progress logging.

The gateway loads ONNX Runtime dynamically (`ORT_DYLIB_PATH`): the Python wheel's copy locally, the official release in Docker.

### 1.2 CPU throughput we measured (and why it matters)

| Stage | Measured | Note |
|---|---|---|
| PDF parsing, pypdf | 57 ms/page | Fast but low quality (see §5.1) |
| PDF parsing, docling | 2.5 s/page (one process); FAR 2,026 pages in ~7 min across 8 processes | Chosen for quality |
| PII masking (Presidio + spaCy lg) | ~30 ms per chunk | The FAR is one document, so it runs as a single slow task in a per-document pool. Results are now cached per document. |
| Dense embedding, nomic v1.5, one process | 6.0 / 7.9 / 11.3 chunks/s at 8 / 16 / 32 threads | Poor thread scaling; ~3.7 GB per process |
| Dense embedding, 5 processes × 4 threads | ~10 chunks/s | No better than one 32-thread process: likely memory-bandwidth limits or contention on the shared machine |
| BM25 sparse vectors | ~3,500 chunks/s | Negligible cost |

- fastembed's built-in `parallel=` mode forces every worker to 1 ONNX thread while each still holds ~3 GB. We replaced it with our own pool of processes × threads.
- Consequence: embedding the full corpus with one model takes tens of minutes on this CPU. Changing the chunker, PII rules or embedding model is an expensive operation, which is why all intermediate results are cached by content hash.

### 1.3 Laptop

The project runs on a laptop with Docker Desktop. The Docker images build and run, and I tested the UI against the Compose stack there. The laptop is **too weak for parsing or embedding the corpus**, so my rule is: no re-embedding there. Consequences are in §2.

---

## 2. Build machine vs laptop: the index bundle

Choice: ingestion is split into a heavy BUILD and a light LOAD, with a checksummed bundle in between
(`data/index_bundle/`). The copy committed to git leaves out the UN and World Bank documents (their terms of use forbid
redistribution); the figures below were measured on the full bundle, which `make build-index` rebuilds.

| Step | Where | What it does | Needs |
|---|---|---|---|
| `make build-index` | Build machine only | Parse → chunk → mask PII → scan for injection → embed → write the bundle | docling, torch, spaCy, Presidio (`ingest` dependency group, ~1.2 GB environment) |
| `make ablation` | Build machine only | Re-embed the whole corpus with alternative models for the M10 comparison | Same |
| `make ingest` | Laptop | Verify checksums and shapes, then load Postgres + Qdrant | Light environment (342 MB), no model inference. Measured in that environment: 11,317 points loaded in 133 s at 615 MB peak memory (embedded Qdrant + Postgres 18.6) |
| `make smoke` | Laptop | 6 real questions through hybrid search with access filters | Query embedding only (cheap). 6/6 passed in the light environment; employee never receives the confidential budget memo, finance gets it as the top 3 |

Limitations this creates
- Bundle size: ~194 MB uncompressed for the full corpus with four embedders. The committed copy (FAR, CUAD and Acme only, nomic-v1.5 vectors only) is ~94 MB: chunks 43 MB, sparse 24 MB, dense 32 MB. The experiment-only embedders (nomic-v1.5-q, bge-small, bge-base) are left out, so re-running `make experiments` needs `make ablation` first (build machine).
- The corpus is frozen on the laptop. Any change to parsing, chunking, PII masking, injection patterns or the embedding model needs a build machine to regenerate the bundle. On the laptop, such a change shows up as a bundle/version mismatch.
- A fresh `git clone` cannot rebuild the full index without heavy hardware. The bundle for FAR, CUAD and Acme is committed (largest file 44 MB), so cloning and running `make ingest` works. Rebuilding it, or adding the UN and World Bank documents back, needs `make build-index` on a big machine. Git LFS wasn't needed.
- The bundle contains the original (unmasked) chunk text, because Postgres stores originals encrypted with the laptop's own key at load time. This adds no new exposure, since the same text is in `data/raw` and `data/synthetic`. The committed bundle only holds FAR, CUAD and synthetic Acme text, so nothing private is published.
- Query-side embeddings for evals and ablations (a few hundred short queries per model) are computed on the laptop. That's small but not free: each model is downloaded once.
- Prompt Guard 2 has not been run over the corpus (no Hugging Face token yet, see §5.4). If added later, it is a full pass over ~11k chunks with an 86M model. That's CPU-heavy and belongs on the build machine.
- The PII and Presidio unit tests are skipped on the laptop (3 of 34), because those libraries are only in the heavy group.
- Postgres and Qdrant tests were run natively, without Docker. Qdrant's embedded mode ignores payload indexes and quantization, so anything that depends on them uses the real server.

---

## 3. Software versions held back

Policy: always the latest stable version; downgrade only for proven incompatibility, and record why.

| Component | Using | Latest | Why held back |
|---|---|---|---|
| Python | 3.14.10 | 3.15.2 | presidio declares `<3.15`; onnxruntime and py-rust-stemmers have no 3.15 wheels |
| torch | 2.14.1 (CPU build) | 2.15.0 | torchvision for 2.15 (0.30) isn't released; 0.29.x fails to load on torch 2.15 (`torchvision::nms does not exist`) |
| torchvision | 0.29.1 (CPU build) | 0.29.2 (PyPI only) | Must come from the same CPU-only index as torch; 0.29.2 isn't on that index yet |
| numpy | 2.4.6 | 2.5.4 | presidio-analyzer caps `numpy<2.5` |
| preshed | 3.0.13 | 3.0.15 | presidio-analyzer caps `<3.0.14` |
| thinc | 8.3.14 | 10.0.0 | spaCy 3.8 caps `<8.4` |
| huggingface-hub | 1.33.0 | 2.4.0 | fastembed and docling-ibm-models cap `<2` |
| semchunk | 3.2.5 | 4.1.1 | docling-core caps `<4` |
| mpmath | 1.3.0 | 1.4.1 | sympy caps `<1.4` |
| Valkey | 9.1.3 | 9.2.0-rc1 | 9.2 is only a release candidate |
| `ort` (Rust ONNX Runtime) | 2.0.0-rc.15 | (no stable 2.x) | Only release candidates exist; we must pin an RC |

Planning assumptions that turned out outdated:
- I assumed `rmcp` (Rust MCP SDK) is pre-1.0; it is actually 3.8.0. MCP stays in Python for other reasons (ADR-01).
- I planned for Postgres 16/17; 18 is current and is used.

---

## 4. Data and corpus

Final build: 177 documents → 11,322 chunks, 2.85M tokens.

| Source | Docs | Chunks | Limitation |
|---|---|---|---|
| FAR (acquisition.gov PDF, 2,026 pages) | 1 | 5,917 | Point-in-time snapshot; the FAR is being rewritten ("Revolutionary FAR Overhaul"). sha256-pinned, so drift is detected rather than silent. |
| World Bank Procurement Regulations, 7th ed. (Sept 2025) | 1 | 375 | Not redistributable: downloaded at build time, never committed |
| UN Procurement Manual | 1 | 591 | Not redistributable (non-commercial terms) |
| CUAD v1 contracts (CC BY 4.0) | 100 of 510 | 3,609 | Deterministic subset of procurement-like contract types; plain text with no headings, so clause headings are guessed by a regex (§5.2) |
| Acme synthetic data (policies, contracts, poisoned docs) | 74 | 830 | Fictional; see below |

Synthetic Acme data
- I had Claude write the 39 policy documents instead of generating them through a free LLM API as I originally planned. They had to be embedded before the laptop move, and I had no API keys at the time. Each passed automated checks:
  - every number in its fact sheet is present;
  - the injection scanner doesn't flag it;
  - no emails or phone numbers;
  - correct header.

  They are still model-written text: the facts were checked, the prose was not. One un-enforced claim exists: the approval-matrix document allows 30-day approval delegation, which the OPA policy doesn't implement.
- Contracts come from one clause template, so they are more uniform than real contracts. Retrieval on them is easier than on real data.
- 500 historical purchase requests come from a log-normal amount model, capped at 8% of the department budget. Only 1 request reaches the 3-level (admin) chain, so admin-level demo data is thin.
- Personal data is Faker-generated (valid-checksum IBANs, `.example` emails). It doesn't reproduce the variety of real-world formats.

Users: 15 instead of the ~12 I planned. Two department managers (Finance, Legal) and a second admin were added, because without them some requests could never be approved (§7).

---

## 5. Ingestion

### 5.1 Parsing
- docling chosen over pypdf after a 20-page FAR comparison. pypdf keeps running page headers, breaks letter spacing ("F IRST") and has no structure. It also crashes (RecursionError) when copying FAR pages. docling is ~40× slower.
- Born-digital PDFs only: OCR is off. A scanned PDF would come out mostly empty.
- docling emits every heading at the same level (`##`), so section depth is inferred from numbering (Part → Subpart → 9.302). Unnumbered documents get flatter section paths.
- The FAR PDF sometimes prints a subpart title *after* its first section. A rule drops ancestors that can't contain the new number, so a few sections (e.g. 9.400, 9.401) end up with no subpart in their path.

### 5.2 Chunking
- ~400 tokens (counted with nomic's own tokenizer), 15% overlap, never across a section. Very short clauses (e.g. FAR definitions, ~30 tokens) become small chunks.
- The CUAD heading regex is heuristic. Contracts with unusual formatting may be chunked by size only.
- Each chunk is embedded with a "contextual header" (title › section path). Retrieval improves, but each chunk's embedding now depends on the document title.

### 5.3 PII masking
- IP addresses are not masked. On CUAD, every "IP address" detected (230 of 230) was a dotted clause number like `5.3.1.1`. Masking them broke clause lookups. A real IP address in a document would therefore pass through.
- Person-name detection runs only on contracts and Acme documents. On regulations it mostly hit job titles (its only "person" in a FAR sample was "SAM", the System for Award Management). A personal name inside a regulation PDF would not be masked.
- Vendor company names are allow-listed, because NER read "Robinson, Cook and Jenkins" as three people. A contact person whose name exactly matches a vendor-name token would stay unmasked.
- US EIN detection needs nearby context words, so a bare `12-3456789` isn't masked (deliberate, to avoid masking clause numbers).
- NER recall is not measured. Presidio + spaCy miss some names; there is no gold PII set yet.

### 5.4 Injection scanning (ingestion side)
- Regex only, so far. Prompt Guard 2 is gated behind the Llama license and needs an `HF_TOKEN`. The ingest report says "regex-only" so it never overstates coverage.
- 5/5 planted attacks caught, 0/172 benign documents flagged, but the patterns were written knowing the 5 attacks. This is not a detection rate. Real attack-success and false-positive rates come from the promptfoo red-team and the benign set in M6.
- Two false-positive classes were found and fixed, and both sentences are now permanent tests. More of this kind are likely in unseen legal text.
  - "act as the agent … The Developer" (contract boilerplate)
  - "There are no restrictions, promises, warranties…" (an entire-agreement clause)
- "Authorized to approve" is normal procurement language. That pattern only flags wording addressed to the reader ("you are authorized…"), and never quarantines by itself.
- Patterns can't match lookarounds or backreferences. That's the price of sharing one pattern file between the Python and Rust regex engines.

### 5.5 Embedding
- nomic-embed-text-v1.5 on CPU at ~10 chunks/s (§1.2) is the main bottleneck. All four models were embedded for the M10 comparison (only nomic-v1.5 is in the committed bundle) (11,322 × each). Times on this machine (8 processes × 4 threads, shared machine):

  | key | model | dim | full-corpus time | chunks/s |
  |---|---|---|---|---|
  | `nomic-v1.5` (primary) | nomic-ai/nomic-embed-text-v1.5 | 768 | ~11 min (new chunks only; rest cached) | ~10-12 |
  | `nomic-v1.5-q` | nomic-ai/nomic-embed-text-v1.5-Q (int8) | 768 | 15 min | 12.6 |
  | `bge-small-v1.5` | BAAI/bge-small-en-v1.5 | 384 | 7 min | 27.6 |
  | `bge-base-v1.5` | BAAI/bge-base-en-v1.5 | 768 | (interrupted by a session restart at 80%, resumed from cache) | n/a |

  Sanity check: all four rank the same chunk first for "What is the hotel cap per night in tier-1 cities?" (`acme-pol-travel#6`), which proves every matrix is aligned with the chunk order. The int8 nomic model was not faster than fp32 here (12.6 vs ~10-12 chunks/s), so on this CPU its benefit is model size, not speed.
- The nomic vectors are not normalized (L2 norm 17.8-23.3; bge vectors are unit length). fastembed returns nomic's raw pooled output.
  - Qdrant is unaffected: the collection uses cosine distance, which normalizes on insert and at query time.
  - Any raw dot-product comparison must L2-normalize nomic vectors first: FAISS inner-product indexes, ablation scripts, and the M3 Rust-vs-Python parity test (compare cosine, not raw values). Otherwise scores are meaningless.
  - Upside for M10: because the vectors are raw, nomic's official Matryoshka recipe (layer-norm over 768 dims → truncate to 256/512 → L2-normalize) can be applied exactly to the stored vectors, with no re-embedding.
- BM25 query vectors are computed in Rust (fastembed-rs has no BM25) and documents in Python: parity verified on 12 fixture queries (identical token-id sets), and dense query vectors match at cosine ≥ 0.999. A tokenizer change on either side must regenerate the fixtures (`brain/ingestion/parity_fixtures.py`).

---

## 6. Vector index: why HNSW (in Qdrant), and how it compares with FAISS

HNSW and FAISS are not alternatives. HNSW is an index *algorithm*. FAISS is Meta's *library* that implements many
algorithms, including HNSW (`IndexHNSWFlat`), IVF, PQ and exact search. The real choices are:
(a) which algorithm, and (b) a vector database (Qdrant) or an in-process library (FAISS).

### 6.1 Index algorithms

| | Flat (exact) | IVF‑Flat | IVF‑PQ | HNSW | DiskANN / Vamana |
|---|---|---|---|---|---|
| How it works | Compares the query with every vector | k-means clusters; scans the `nprobe` nearest clusters | IVF + vectors compressed into short codes | Multi-layer proximity graph; greedy walk from sparse top layers down to the dense bottom layer | Single graph designed for SSD; compressed vectors in RAM, full vectors on disk |
| Recall | 100% (ground truth) | High, set by `nprobe` | Lower (compression loss), often fixed by re-ranking | Very high (≥0.95-0.99 typical), set by `ef` | High |
| Query latency | Linear in data size | Sub-linear | Sub-linear, very fast scans | ~Logarithmic; among the fastest in-memory | Low ms, bounded by SSD reads |
| Memory per 768-d vector | 3,072 B (float32) | 3,072 B | ~64-96 B (30-50× smaller) | 3,072 B + ~128 B of graph links at M=16 | Small in RAM; full vectors on SSD |
| Build cost | None | k-means training | k-means + codebook training | Slowest (graph construction) | Slow |
| Inserts / deletes | Trivial | Easy | Easy until data drift forces retraining | Inserts easy; deletes leave tombstones | Possible, more complex |
| Restrictive filters | Fine (skip excluded vectors) | Fine (skip excluded ids while scanning) | Fine | Fragile if naive: excluded nodes break up the graph and recall collapses | Needs dedicated filtered variants |
| Sweet spot | < ~1M vectors, or as ground truth | 1M to 100M, RAM available | 100M–billions, memory-bound | 1M to ~100M, in RAM, low latency, high recall | 100M–billions on SSD |

On public in-memory benchmarks (ann-benchmarks), HNSW-family indexes are at or near the best recall-vs-speed
trade-off at million scale. At billion scale, compression (IVF‑PQ) or disk-based graphs (DiskANN) win,
because HNSW with full float32 vectors in RAM is too expensive: 1B × 768-d ≈ 3 TB for the vectors alone.

### 6.2 The system: Qdrant vs FAISS

| | Qdrant (chosen) | FAISS |
|---|---|---|
| What it is | Vector database (Rust server) | C++/Python library, in-process |
| Indexes | HNSW; quantization: int8 (4×), binary (32×), PQ | Flat, IVF, PQ/OPQ, HNSW, scalar quantization, GPU indexes |
| Persistence, create/update/delete, snapshots | Built in | You build them (save/load index files) |
| Metadata per vector (department, classification) | Yes, with payload indexes | No: keep an id → metadata map yourself |
| Filtered search | Filter-aware HNSW (extra links for indexed fields) + planner switches to exact search for small filtered sets | `IDSelector`: fine for IVF/flat; HNSW + restrictive filters can lose recall |
| Sparse vectors (BM25) + hybrid fusion | Native sparse index; both branches in one batch call (RRF done in the gateway, see below) | None |
| Rescoring with full vectors | Built in (int8 in RAM, float32 on disk) | Manual |
| GPU | Indexing only (recent versions) | Strong GPU search (IVF, PQ, CAGRA) |
| Sharding / replication | Built in | You build it (or use Milvus) |
| Latency overhead | One network hop (~0.2-1 ms locally) | None |

### 6.3 Which is best at scale: depends on the binding constraint

- RAM available, need low latency + high recall (≤ ~100M vectors): HNSW. Add int8 quantization + rescoring for ~4× less memory at small recall cost; that's our configuration.
- Memory-bound at 100M+: IVF‑PQ (FAISS or Milvus) or DiskANN on SSD.
- GPU batch search, offline dedup or clustering, read-only index: FAISS on GPU.
- Heavy metadata filtering (permissions, tenants), frequent updates, hybrid search: a database with filter-aware indexing (Qdrant). Raw FAISS loses here: filtering, persistence and sparse search all become your problem.

### 6.4 Why we chose it, and the honest limitation at our scale
- Access control must happen inside the search.
  - Filtering *after* the search drops results after ranking and can return fewer than top-k.
  - Naive HNSW *pre*-filtering loses recall when filters are restrictive.
  - Qdrant's filter-aware HNSW and its exact-search fallback handle both. The access filter goes on both the dense and BM25 branches.
- Hybrid in one engine: dense top-40 + BM25 top-40 in one batch call, RRF-fused in the gateway. FAISS has no sparse side.
- Qdrant's server-side RRF is not deterministic on ties. Dense rank *i* and BM25 rank *i* always score the same, and the
  server returned them in varying order: the same query gave a different top-20 on 24 of 30 repeats, and eval MRR moved
  by ±0.02 between identical runs. Fusion now happens in the gateway with a fixed tie-break (best single-branch rank,
  then chunk id). Cost: about 2× payload transfer (both branches return payloads, ~80 instead of 20), which is negligible locally.
- A memory-scaling path without changing systems: int8 in RAM, float32 on disk, rescoring.
- Limitation: our corpus is only 11,322 chunks. Exact search is ~8.7M multiply-adds per query, about a millisecond on CPU. HNSW is not needed for speed at this size. It's chosen because the same design holds with filters at millions of vectors. M10 measures HNSW (`hnsw_ef` sweep) against exact search; FAISS baselines (Flat, IVF‑Flat, IVF‑PQ, HNSW) on the bundle's vectors are an optional addition.
- Qdrant's payload indexes and int8 quantization are now exercised on the real server (§2). Still to measure: recall of int8 vs fp32 and `hnsw_ef` vs exact (M10).

---

## 7. Authorization model: interpretations and gaps

| Item | Choice | Limitation |
|---|---|---|
| Retrieval scope | OPA returns per-classification grants `{classification: [departments]}`, not the original two flat lists | Deviation from the original plan (the flat lists' cross product is wrong both ways); Qdrant payload has no `allowed_roles[]` field |
| "≤ $1k auto-manager" | Read as auto-approval (empty chain) | The original wording was ambiguous |
| Personal approval limits | Bind only when the approver is the *final* level; otherwise a higher level signs after | Interpretation; resolves the original conflict between "manager ≤ $25k" and "> $1k needs manager" |
| Escalation | Finance may act at the manager level (no-manager departments, a manager's own request, over-limit) | One person signs at most one level (DB unique constraint), but Finance's reach is wider than a strict reading of the original brief |
| Separation of duties | Approver role must equal the level (except Finance at manager level); admins don't override lower levels | No break-glass override path |
| `spend_limit` | Stored in user records and claims, not enforced | Only the remaining department budget; I still need to confirm the intended meaning |
| Delegation | Mentioned in the generated approval-matrix document (30 days) | Not modeled in OPA; the assistant may quote a rule the system doesn't enforce |
| Admin-level demo data | 1 historical request reaches the admin level | Thin coverage for admin-level evals; agent tasks will need to create more |

---

## 8. External services and cost

- Offline numbers are pipeline numbers, not model numbers. Without keys, the LLM tiers and Jev are served by
  `mock-llm` (extractive answers; keyword/overlap/regex mock Jev). Latencies and behaviour of the gateway are real;
  answer quality, routing accuracy and gate precision measured this way say nothing about the real models.
- Metric names deviate from my original plan for cost: `llm_cost_usd_micros_total` / `jev_cost_usd_micros_total` hold integer
  micro-dollars (the `metrics` crate's counters are integers). Dashboards divide by 1e6.
- Document PII stays masked for every role in answers. Ingestion stored encrypted originals but not the
  placeholder→value maps, so only placeholders from the user's *own* query can be unmasked (finance/admin).

- Free LLM tiers set the throughput ceiling (Groq, Gemini, OpenRouter: per-minute and per-day limits). Load tests therefore use a mock LLM; real-LLM latency is reported separately.
- Gemini's free tier may use prompts to improve Google's products. Only public or synthetic data is ever sent. The masked Acme data is synthetic.
- TypeSafe Jev is a hosted, closed decision model:
  - It's a vendor dependency, and its benchmarks are vendor-reported. The system has a deterministic degraded mode if Jev is unavailable.
  - Only PII-masked text is sent; zero data retention is enterprise-only.
  - Prices and the $5 credit are unverified (§9).
- No public hosting: local Docker Compose plus a recorded video. Free container hosts can't run this stack (e.g. Render's 512 MB).
- Policy documents were not generated through the free API (§4), so the planned "LLM-generated via free API" provenance doesn't apply to them.

---

## 9. Open items not yet verified

| Item | Blocked on | Risk if wrong |
|---|---|---|
| `jev-1.14.0` model id exists (public OpenAPI lists only `jev-latest`, `jev-preview`) | TypeSafe API key | Router/gate must pin a different id |
| TypeSafe $5 credit and per-org rate limits | My TypeSafe console | Budget meter thresholds |
| Current free models and limits on Groq / Gemini / OpenRouter | API keys | `providers.toml` tiers |
| Prompt Guard 2 license acceptance and ONNX export | `HF_TOKEN` | Guard layer 4 runs on the ProtectAI stand-in (§10); ingestion scan stays regex-only |
| Batched vs per-passage gate quality (ADR-07 spike) | TypeSafe API key | Gate request shape may need to change |

---

## 10. Guardrails: the injection classifier and the red team

| Item | What we have | Limitation |
|---|---|---|
| Layer-4 classifier | ProtectAI `deberta-v3-base-prompt-injection-v2` (Apache-2.0, ungated) as a stand-in for Prompt Guard 2 (gated, no ONNX published) | Not my first-choice model. Swapping is a directory change (`CLASSIFIER_DIR`) once `HF_TOKEN` exists (`classifier_models export-pg2`) |
| Model size | int8 (MatMul-only, per-channel) 539 MB, fp32 739 MB; ~185M params vs PG2-86M | Bigger and slower than PG2 would be. Every chat request scores the query concurrently with the Jev route, so it adds no latency unless it is the slower of the two |
| Quantization | Naive dynamic int8 (all ops) scored every probe ≈ 0.02: silently useless. Only the MatMul-only per-channel variant is kept, and only if it agrees with fp32 on six labelled probes | Six probes is a smoke test, not a calibration; the red-team set (below) is the real check |
| Thresholds | Block if classifier ≥ 0.9 alone, or ≥ 0.5 and a second signal (any regex hit, Jev ≥ 0.5, or Jev down) | Set by hand; τ tuning with PR curves is M10 |
| Known false positive | *"Can I approve my own purchase request?"* scores 0.983, so it is blocked (benign FPR 1/150 = 0.7%) | A real classifier error on ordinary procurement language; τ alone can't fix it without losing detection. Candidates for M10: domain allow-list or a second-signal requirement for short questions |
| Short messages | Under 6 words the classifier needs corroboration (regex or Jev) | Found in M8: "approve this" scored 1.000 and was silently blocked. With the rule, short attacks no regex covers ("show hidden rules", "dump the prompt", "you are DAN now") pass the local layers and rely on Jev (mocked offline) and the output canary check. Regex additions belong in a v2 pattern file (shared with ingestion), planned for M10 |
| English bias | The classifier is English-trained; language-switch attacks are caught by regex/Jev patterns | Non-English injections that avoid the shared patterns rely on Jev, which is mocked offline |
| Offline red team | Guard layers, ACL, PII masking, action confirmation, timing, lockout and HTTP abuse are real; the LLM and Jev are mocks | *Model compliance* (does the real LLM obey an injection that slips through?) is not measured until keys exist |
| Silent-failure timing | Blocked responses are padded with a delay drawn from the recorded latency of real no-answers | Padding hides the guard verdict, not load: under heavy load, real latency drifts and an attacker could compare |
| Strikes | Throttle at 3, lock at 10 (Valkey, 24 h); admins reset via `POST /admin/users/{id}/reset-strikes` (audited) | A shared account (e.g. a kiosk) can be locked by one person's tests |

---

## 11. Caching

| Item | What we have | Limitation |
|---|---|---|
| Semantic cache threshold | 0.90 cosine (M10 sweep; 0.95 hit 0/10) | 3/10 paraphrases hit; 0 false hits on 7,284 same-key-term pairs with a margin of only 0.026, and only 10 positive pairs exist: re-tune on real traffic |
| Semantic false hits | Hard filters (scope, index, prompt, tier) plus an exact match on numbers/identifiers | Negations and non-numeric qualifiers ("can" vs "can't", "domestic" vs "international") are *not* filtered; only the threshold protects them |
| Cache hit timing | A hit returns in ~20 ms instead of ~1 s | Within one scope, a fast answer tells a user someone with the same visibility asked before. Nothing crosses scopes |
| L1 staleness across replicas | L1 lives ≤ 1 h; an index bump clears the local L1 within one poll (10 s) | After an admin flush, *other* replicas keep L1 entries until their TTL (single replica here) |
| Offline isolation test | 10 probes, scored on cache hits, forbidden sources and copied text | With the mock LLM, the other department still gets *an* answer from its own documents; a real LLM would mostly abstain. The gate checks the cache, not answer quality |
| Valkey down | Each L2 miss costs one failed call; L1 still serves | The semantic cache is in Qdrant and keeps working, slightly unlike my original plan to bypass both L2 and the semantic cache |
| Degraded answers | Never written (Jev, OPA, LLM or classifier degraded) | With the classifier unavailable, nothing is cached at all |

---

## 12. UI, observability, load and resilience

| Item | What we have | Limitation |
|---|---|---|
| Retrieval latency target (p95 < 100 ms at 100 VUs) | Not met on CPU. One request: p50 72 ms, p95 82 ms (embed ~35 ms, Qdrant hybrid ~5 ms). At 100 VUs: ~55-70 RPS, p95 1.7-2.0 s | Query embedding is CPU-bound (4 nomic instances × 4 threads on a *shared* 64-core host, load average 12-35 during runs). Options: more instances on a dedicated host, a GPU, a smaller/int8 query model or Matryoshka dims (M10 measures quality), and the embedding cache (real traffic repeats) |
| Cache-hit throughput (1-5k RPS) | 4.7-5.9k RPS, p95 19-26 ms at 64 VUs | Single gateway process; numbers vary with machine load |
| Classifier on exact cache hits | An exact answer-cache hit doesn't wait for guard layer 4 | Same normalized text, same scope, already answered after all guards: nothing new can leak. Such a request earns no strike even if it scores high |
| Classifier under load | Pool of 4 sessions × 4 threads; a timeout (1.5 s) is degraded: counted (`classifier_skipped_total`), alerted, never cached | With Jev also down, a timed-out request has only the regex layer. Before M9 a timeout silently counted as "clean" (fail-open under load) |
| Readiness | `/readyz` waits for the embedder and the classifier load attempt | A missing classifier model still lets the gateway become ready (degraded + alert), by design |
| Native vs compose observability | Native stack: Prometheus 3.15.1 + Grafana 13.3.0, dashboards and datasource provisioned and verified | Compose mounts the same provisioning into `otel-lgtm` at *assumed* paths (`/otel-lgtm/grafana/conf/provisioning/...`); unverified until the first `make up` on the laptop. A separate `prometheus` container scrapes the gateway (the original plan listed only otel-lgtm) |
| Traces | In-memory ring of the last 1,000 chat requests (stage spans, diagnostics; no text) | Not the OpenTelemetry trace: lost on restart, per replica. No Tempo on the HPC, so `trace_id` and the Grafana link are empty there |
| Insights cost KPIs | Shadow = every answer priced at the expensive tier with no cache and no router | Offline, mock Jev spend exceeds the mock LLM savings, so *actual > shadow*; the page says so plainly rather than inventing savings |
| Lighthouse | axe-core: 0 WCAG 2.1 AA violations on all pages, light and dark (logged-in, real Chromium) | Lighthouse itself scored only the pages reachable without a session |
| Agent-task harness | Offline scripted directives go in a `script` field (EVAL_ENDPOINTS only), outside the guarded message | Found in M8: since M6 the classifier (rightly) blocked typed `[[call ...]]` directives, so 9/37 tasks had silently become guard tests |
| Chaos drills | `make chaos` stops real processes for every row of the resilience matrix and probes the public API (docs/chaos-drills.md) | Native mode verified; `--mode compose` drives `docker compose stop/start` but is untested here |

