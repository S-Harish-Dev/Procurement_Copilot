"""Dense (nomic-embed-text-v1.5) + sparse (BM25) embeddings via fastembed (local ONNX, $0).

What gets embedded is a *contextual* text: document title + section path + chunk.
A chunk like "(b) 30 days' written notice" is meaningless alone; with
"Northwind Freight Agreement › 6. Termination" in front it retrieves correctly,
and BM25 can match clause numbers that only appear in the section path.

nomic v1.5 requires task prefixes: "search_document: " here, "search_query: " at query time
(the Rust gateway must use the same prefix — parity test in M3).

Parallelism: fastembed's own `parallel=` mode pins every worker to ONE onnxruntime thread
(fastembed/text/onnx_embedding.py) while each still holds ~3 GB. We run our own pool of
P processes x T threads, with P capped by free memory — this is a shared machine.
Embeddings are cached on disk by sha256 of the exact input text, per model.
"""

import hashlib
import os
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from fastembed import SparseTextEmbedding, TextEmbedding

from common.resources import capped_workers

DENSE_MODEL = "nomic-ai/nomic-embed-text-v1.5"
SPARSE_MODEL = "Qdrant/bm25"
DENSE_DIM = 768
DOC_PREFIX = "search_document: "
EMBED_VERSION = f"{DENSE_MODEL}|{SPARSE_MODEL}|ctx-v1"
BATCH = 64


@dataclass(frozen=True)
class DenseModel:
    """A dense embedder as used for documents and (at query time) for queries."""

    key: str  # short name used for bundle file names
    model: str  # fastembed model id
    dim: int
    doc_prefix: str
    query_prefix: str
    gb_per_worker: float  # measured/estimated peak RSS per embedding process


PRIMARY = DenseModel("nomic-v1.5", DENSE_MODEL, DENSE_DIM, DOC_PREFIX, "search_query: ", 4.0)

# Alternatives embedded once on the build machine for the M10 embedder ablation.
ABLATION: list[DenseModel] = [
    DenseModel("nomic-v1.5-q", "nomic-ai/nomic-embed-text-v1.5-Q", 768, DOC_PREFIX, "search_query: ", 2.5),
    DenseModel(
        "bge-small-v1.5",
        "BAAI/bge-small-en-v1.5",
        384,
        "",
        "Represent this sentence for searching relevant passages: ",
        1.5,
    ),
    DenseModel(
        "bge-base-v1.5",
        "BAAI/bge-base-en-v1.5",
        768,
        "",
        "Represent this sentence for searching relevant passages: ",
        2.5,
    ),
]


def contextual_text(title: str, section_path: str, text: str) -> str:
    header = f"{title} › {section_path}" if section_path else title
    return f"{header}\n\n{text}"


@dataclass
class SparseVec:
    indices: list[int]
    values: list[float]


_model: TextEmbedding | None = None
_prefix = DOC_PREFIX


def _init(threads: int, model: str = DENSE_MODEL, prefix: str = DOC_PREFIX) -> None:
    global _model, _prefix
    _model = TextEmbedding(model, threads=threads)
    _prefix = prefix


def _embed_batch(texts: list[str]) -> np.ndarray:
    assert _model is not None
    return np.asarray(list(_model.embed([_prefix + t for t in texts], batch_size=16)), dtype=np.float32)


class Embedder:
    def __init__(
        self, cache_dir: Path, processes: int, threads: int = 4, model: DenseModel = PRIMARY
    ) -> None:
        self.model = model
        # Cache key = model + sparse model + context-format version (for the primary model this is
        # exactly EMBED_VERSION, so existing caches stay valid).
        key = f"{model.model}|{SPARSE_MODEL}|ctx-v1"
        self.cache_dir = cache_dir / "embeddings" / hashlib.sha256(key.encode()).hexdigest()[:12]
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.processes = capped_workers(processes, model.gb_per_worker)
        self.threads = max(1, min(threads, (os.cpu_count() or 4) // self.processes))
        self.sparse = SparseTextEmbedding(SPARSE_MODEL)

    def _path(self, text: str) -> Path:
        h = hashlib.sha256(text.encode()).hexdigest()
        return self.cache_dir / h[:2] / f"{h}.npy"

    def dense_docs(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.model.dim), dtype=np.float32)
        todo = []
        for i, t in enumerate(texts):
            p = self._path(t)
            if p.exists():
                out[i] = np.load(p)
            else:
                todo.append(i)
        if todo:
            batches = [todo[i : i + BATCH] for i in range(0, len(todo), BATCH)]
            init_args = (self.threads, self.model.model, self.model.doc_prefix)
            with ProcessPoolExecutor(self.processes, initializer=_init, initargs=init_args) as pool:
                for idx, vecs in zip(
                    batches, pool.map(_embed_batch, [[texts[i] for i in b] for b in batches]), strict=True
                ):
                    for i, v in zip(idx, vecs, strict=True):
                        out[i] = v
                        p = self._path(texts[i])
                        p.parent.mkdir(exist_ok=True)
                        np.save(p, v)
        return out

    def sparse_docs(self, texts: list[str]) -> list[SparseVec]:
        # BM25 term frequencies only; Qdrant applies IDF at query time (modifier=idf on the collection).
        # ~3,500 chunks/s single-process — not worth parallelising.
        return [
            SparseVec(e.indices.tolist(), e.values.tolist()) for e in self.sparse.embed(texts, batch_size=256)
        ]
