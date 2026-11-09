# ADR-05: nomic-embed-text-v1.5 + BM25 hybrid in Qdrant, with Rust/Python parity tests

- Status: accepted (2026-10-30)

## Decision
- Documents embedded once in Python (fastembed, `search_document: ` prefix, contextual header = title ›
  section path); queries embedded in the Rust gateway (fastembed-rs, `search_query: ` prefix).
- BM25 sparse vectors: fastembed `Qdrant/bm25` for documents; reimplemented in Rust for queries because
  fastembed-rs 7.5 has no BM25 model.
- One Qdrant batch query: dense top-40 + BM25 top-40, the same ACL filter on both branches
  (`should` per classification grant + `must` active index version), fused with RRF (k = 2, Qdrant's default) **in the
  gateway**, top-20. *Amended in M6:* server-side `Fusion::Rrf` broke ties in arbitrary order (24/30 repeats differed),
  so fusion moved to `retrieval::search::rrf` with a deterministic tie-break.

## Parity (the risk this ADR exists to control)
Two languages embed the two sides of every similarity. Fixtures generated from Python
(`brain/ingestion/parity_fixtures.py`) are asserted by Rust tests:
- BM25: identical sorted token-id sets for 12 queries (Unicode, clause numbers, IDs, stopword-only): pass.
- Dense: cosine ≥ 0.999 between Rust and Python query vectors (opt-in test, downloads the model): pass.

## Facts worth knowing
- fastembed returns raw (unnormalized) nomic vectors; the cosine collection normalizes, but any raw
  dot-product use must normalize first (Rust `QueryEmbedder` returns unit vectors).
- ONNX Runtime is loaded dynamically (`ORT_DYLIB_PATH`): the prebuilt static library needs glibc ≥ 2.38.

## Alternatives
- *Dense only*: misses exact IDs/clause numbers ("52.212-4", "C-0007"). *BM25 only*: misses paraphrase.
- *SPLADE (fastembed-rs has it)*: better than BM25 in benchmarks, but a second neural model on the hot path and
  a heavier ingestion; candidate for the M10 ablation.
