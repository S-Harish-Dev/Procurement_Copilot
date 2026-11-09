# ADR-04: Qdrant (HNSW, int8 scalar quantization with rescoring) as the vector store

- Status: accepted (2026-10-30), measured in M10 · Detail: Limitation.md §6

## Decision
- Qdrant 1.20 server; collection `chunks` with named dense vector `dense` (768-d, cosine, HNSW m=16,
  ef_construct=128) and sparse vector `bm25` (IDF modifier). Scalar int8 quantization `always_ram`, float32
  originals on disk, rescoring on. Payload indexes on `department`, `classification`, `index_version`,
  `injection_flag` (keyword) so the ACL filter is applied *inside* the search.
- Postgres stays the system of record for transactional data; Qdrant holds only masked chunk text + metadata.
- Every chunk carries `index_version`; a rebuild loads a new version and flips the active row in Postgres.

## Why
- Access control must happen inside the search. Post-filtering drops ranked results (fewer than top-k);
  naive HNSW pre-filtering loses recall on restrictive filters. Qdrant's filter-aware HNSW plus its exact-search
  fallback for small filtered sets handles both, and the same filter goes on the dense and the BM25 branch.
- Hybrid in one engine: native sparse vectors, so dense top-40 + BM25 top-40 is one batch call.
- Scaling path without changing systems: int8 in RAM (≈4× smaller), float32 on disk for rescoring.

## Measured (M10, `evals/experiments/{hnsw_ef,quantization}.json`; 185 answerable cases, ACL-scoped)
| Variant | share of exact top-20 | recall@20 | p50 ms (incl. HTTP + JSON) |
|---|---|---|---|
| hnsw_ef = 8 … 256 | 1.00 at every ef | 0.895 | 43.8-43.9 |
| int8 + rescore (production) | 1.00 | 0.895 | 43.88 |
| int8, no rescore | 1.00 | 0.895 | 43.88 |
| fp32 HNSW | 1.00 | 0.895 | 43.89 |
| exact (brute force, fp32) | n/a | 0.895 | 43.82 |

ACL-filtered candidate sets are small, so Qdrant's planner effectively searches exactly; `ef` changes nothing at
this scale and latency is dominated by the request (a 768-float JSON body), not the graph.

## Honest limitation
The corpus is 11,322 chunks: exact search is ~8.7M multiply-adds per query (about a millisecond on CPU). HNSW is
not needed for speed here; it is chosen because the same design holds with filters at millions of vectors.

## Alternatives rejected
- *FAISS (in-process library)*: no payload filtering, persistence, sparse vectors or replication; HNSW + restrictive
  `IDSelector` filters can lose recall. Better for GPU batch search over a read-only index.
- *IVF-PQ / DiskANN*: the right choice when memory-bound at 100M+ vectors; unnecessary at 11k.
- *pgvector*: one database fewer, but no native sparse/hybrid query and weaker filtered-ANN behaviour.

## Found while measuring
Qdrant's server-side `Fusion::Rrf` returned equal-score ties in arbitrary order (24/30 repeats changed the top-20),
so fusion moved into the gateway (ADR-05 amendment).
