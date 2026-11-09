from pathlib import Path

import numpy as np
import pytest
from qdrant_client import QdrantClient, models

from ingestion import bundle, store
from ingestion.embed import SparseVec
from ingestion.store import ChunkRecord


def _tiny(root: Path) -> None:
    docs = [
        {
            "id": "pub",
            "source": "acme",
            "title": "Public",
            "path": "p",
            "sha256": "x",
            "department": "all",
            "classification": "public",
            "effective_date": None,
        },
        {
            "id": "conf",
            "source": "acme",
            "title": "Conf",
            "path": "c",
            "sha256": "y",
            "department": "Finance",
            "classification": "confidential",
            "effective_date": None,
        },
    ]
    chunks = [
        ChunkRecord(f"{d}#0", d, 0, "1. Scope", f"masked {d}", f"original {d}", 3, False, None, q)
        for d, q in (("pub", False), ("conf", False))
    ] + [ChunkRecord("pub#1", "pub", 1, "2. Bad", "bad", "bad", 1, True, None, True)]
    rng = np.random.default_rng(0)
    dense = rng.normal(size=(3, 768)).astype(np.float32)
    bundle.write(
        root,
        docs,
        chunks,
        ["a", "b", "c"],
        dense={"nomic-v1.5": ({"model": "m", "doc_prefix": "", "query_prefix": ""}, dense)},
        sparse=[([1, 2], [1.0, 1.0]), ([2, 3], [1.0, 1.0]), ([9], [1.0])],
        meta={"versions": {}, "primary_dense": "nomic-v1.5", "sparse_model": "Qdrant/bm25"},
    )


def test_roundtrip_and_tamper_detection(tmp_path: Path) -> None:
    _tiny(tmp_path)
    b = bundle.read(tmp_path)
    assert len(b.chunks) == 3 and b.embed_texts == ["a", "b", "c"]
    assert b.dense("nomic-v1.5").shape == (3, 768)
    (tmp_path / "chunks.jsonl").write_text(
        (tmp_path / "chunks.jsonl").read_text().replace("masked pub", "evil")
    )
    with pytest.raises(ValueError, match="checksum"):
        bundle.read(tmp_path)


def test_add_dense_reseals_manifest(tmp_path: Path) -> None:
    _tiny(tmp_path)
    bundle.add_dense(tmp_path, "bge-small-v1.5", {"model": "b"}, np.ones((3, 384), dtype=np.float32))
    b = bundle.read(tmp_path)
    assert b.dense("bge-small-v1.5").shape == (3, 384)


def test_load_into_embedded_qdrant_quarantine_and_acl(tmp_path: Path) -> None:
    _tiny(tmp_path / "b")
    b = bundle.read(tmp_path / "b")
    client = QdrantClient(path=str(tmp_path / "q"))
    store.ensure_collection(client)
    dense = b.dense("nomic-v1.5")
    n = 0
    for doc in b.documents:
        idx = [i for i, c in enumerate(b.chunks) if c.doc_id == doc["id"]]
        n += store.upsert_points(
            client, doc, [b.chunks[i] for i in idx], dense[idx], [SparseVec(*b.sparse[i]) for i in idx], 1
        )
    assert n == 2  # the quarantined chunk never reaches Qdrant

    # Hybrid query with the ACL filter on BOTH branches (employee: public only).
    acl = models.Filter(
        must=[models.FieldCondition(key="classification", match=models.MatchValue(value="public"))]
    )
    res = client.query_points(
        "chunks",
        prefetch=[
            models.Prefetch(query=dense[1].tolist(), using="dense", filter=acl, limit=10),
            models.Prefetch(
                query=models.SparseVector(indices=[2, 3], values=[1.0, 1.0]),
                using="bm25",
                filter=acl,
                limit=10,
            ),
        ],
        query=models.FusionQuery(fusion=models.Fusion.RRF),
        limit=10,
    )
    assert {(p.payload or {}).get("doc_id") for p in res.points} == {"pub"}  # confidential doc never returned
