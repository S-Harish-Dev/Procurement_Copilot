"""Write Rust<->Python parity fixtures for query-side embeddings.

Documents were embedded in Python; queries are embedded in the Rust gateway. If the two sides
disagree, retrieval silently degrades, so the gateway's tests compare against these fixtures:
- BM25: the exact sorted token-id set fastembed's `Qdrant/bm25` produces for each query
  (fastembed-rs has no BM25 model, so the gateway reimplements it).
- Dense: nomic-embed-text-v1.5 query vectors ("search_query: " prefix), L2-normalised; the Rust
  side must reach cosine >= 0.999.

    uv run python -m ingestion.parity_fixtures
"""

import json

import numpy as np
from fastembed import SparseTextEmbedding, TextEmbedding

from common.settings import REPO_ROOT
from ingestion.embed import PRIMARY, SPARSE_MODEL

OUT = REPO_ROOT / "gateway" / "crates" / "retrieval" / "testdata"

QUERIES = [
    "Who has to approve a purchase request of USD 15,000?",
    "What is the hotel cap per night in tier-1 cities?",
    "When is first article testing and approval appropriate under FAR 9.302?",
    "What does clause 52.212-4 require?",
    "What is the renewal notice period in contract C-0007?",
    "Can I ignore the three-quote rule for emergency purchases??!",
    "Procurement savings target FY2026 — 6% of addressable spend",
    "naïve café résumé façade coöperate",
    "the and of to a in",
    "Running runners ran quickly; connections connected connecting",
    "IBAN payments, wire transfers & bank-detail changes (call-back)",
    "Supplier's taxpayer identification number",
]

DENSE_QUERIES = QUERIES[:5]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    sparse = SparseTextEmbedding(SPARSE_MODEL)
    bm25 = []
    for q in QUERIES:
        e = next(iter(sparse.query_embed(q)))
        bm25.append({"query": q, "indices": sorted(int(i) for i in e.indices)})
    (OUT / "bm25_parity.json").write_text(json.dumps(bm25, indent=1) + "\n")

    dense = TextEmbedding(PRIMARY.model)
    vecs = np.asarray(list(dense.embed([PRIMARY.query_prefix + q for q in DENSE_QUERIES])), dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    rows = [
        {"query": q, "vector": [round(float(x), 6) for x in v]}
        for q, v in zip(DENSE_QUERIES, vecs, strict=True)
    ]
    (OUT / "dense_parity.json").write_text(json.dumps(rows) + "\n")
    print(f"wrote {len(bm25)} bm25 and {len(rows)} dense fixtures to {OUT}")


if __name__ == "__main__":
    main()
