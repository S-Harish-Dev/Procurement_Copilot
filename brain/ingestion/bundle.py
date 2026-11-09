"""The index bundle: everything the build machine computed, in a form a laptop can load without models.

Layout (data/index_bundle/):
  manifest.json        versions, counts, embedding models (+ prefixes), sha256 of every file
  documents.jsonl      one row per document (access-control metadata)
  chunks.jsonl         one row per chunk, in matrix order: masked + original text, flags, and
                       `embed_text` — the exact string that was embedded (used for Rust parity tests)
  sparse_bm25.jsonl    BM25 term-frequency vectors, same order
  dense/<key>.npy      float32 [n_chunks, dim] per dense model, same order

The bundle contains the ORIGINAL chunk text (it is re-encrypted into Postgres at load time). That
text is derivable from data/raw + data/synthetic anyway, so the bundle adds no new exposure. The copy
committed to git leaves out the UN and World Bank documents (terms of use forbid redistribution): see
`excluded_sources` in manifest.json; `make build-index` produces the full bundle.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from ingestion.store import ChunkRecord

FORMAT_VERSION = 1


@dataclass
class Bundle:
    manifest: dict[str, Any]
    documents: list[dict[str, Any]]
    chunks: list[ChunkRecord]
    embed_texts: list[str]
    sparse: list[tuple[list[int], list[float]]]
    root: Path

    def dense(self, key: str) -> np.ndarray:
        return np.asarray(np.load(self.root / "dense" / f"{key}.npy"), dtype=np.float32)


def _sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w") as f:
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open() as f:
        return [json.loads(line) for line in f]


def write(
    root: Path,
    documents: list[dict[str, Any]],
    chunks: list[ChunkRecord],
    embed_texts: list[str],
    dense: dict[str, tuple[dict[str, Any], np.ndarray]],
    sparse: list[tuple[list[int], list[float]]],
    meta: dict[str, Any],
) -> None:
    (root / "dense").mkdir(parents=True, exist_ok=True)
    _write_jsonl(root / "documents.jsonl", documents)
    _write_jsonl(
        root / "chunks.jsonl",
        [{**asdict(c), "embed_text": t} for c, t in zip(chunks, embed_texts, strict=True)],
    )
    _write_jsonl(root / "sparse_bm25.jsonl", [{"indices": i, "values": v} for i, v in sparse])
    models = {}
    for key, (info, matrix) in dense.items():
        assert matrix.shape[0] == len(chunks), f"{key}: {matrix.shape[0]} rows != {len(chunks)} chunks"
        np.save(root / "dense" / f"{key}.npy", matrix.astype(np.float32))
        models[key] = {**info, "dim": int(matrix.shape[1]), "file": f"dense/{key}.npy"}
    manifest = {
        "format_version": FORMAT_VERSION,
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        **meta,
        "counts": {"documents": len(documents), "chunks": len(chunks)},
        "dense_models": models,
    }
    _seal(root, manifest)


def add_dense(root: Path, key: str, info: dict[str, Any], matrix: np.ndarray) -> None:
    """Add (or replace) one dense matrix, e.g. an ablation embedder computed later on the build machine."""
    manifest = json.loads((root / "manifest.json").read_text())
    assert matrix.shape[0] == manifest["counts"]["chunks"]
    np.save(root / "dense" / f"{key}.npy", matrix.astype(np.float32))
    manifest["dense_models"][key] = {**info, "dim": int(matrix.shape[1]), "file": f"dense/{key}.npy"}
    _seal(root, manifest)


def _seal(root: Path, manifest: dict[str, Any]) -> None:
    files = ["documents.jsonl", "chunks.jsonl", "sparse_bm25.jsonl"] + [
        m["file"] for m in manifest["dense_models"].values()
    ]
    manifest["files"] = {name: _sha(root / name) for name in files}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")


def read(root: Path, verify: bool = True) -> Bundle:
    manifest = json.loads((root / "manifest.json").read_text())
    if manifest.get("format_version") != FORMAT_VERSION:
        raise ValueError(f"bundle format {manifest.get('format_version')} != supported {FORMAT_VERSION}")
    if verify:
        for name, digest in manifest["files"].items():
            if _sha(root / name) != digest:
                raise ValueError(f"bundle file {name} does not match its manifest checksum")
    rows = _read_jsonl(root / "chunks.jsonl")
    embed_texts = [r.pop("embed_text") for r in rows]
    return Bundle(
        manifest=manifest,
        documents=_read_jsonl(root / "documents.jsonl"),
        chunks=[ChunkRecord(**r) for r in rows],
        embed_texts=embed_texts,
        sparse=[(r["indices"], r["values"]) for r in _read_jsonl(root / "sparse_bm25.jsonl")],
        root=root,
    )
