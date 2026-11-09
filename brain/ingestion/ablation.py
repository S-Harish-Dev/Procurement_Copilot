"""Embed the corpus with the alternative models used in the M10 embedder ablation (build machine only).

Uses exactly the same contextual texts as the primary model (bundle `embed_text`), each model with
its own document prefix, and adds dense/<key>.npy to the bundle. Query-side embeddings for the eval
set (a few hundred short queries) are cheap and computed at eval time.

    uv run --group ingest python -m ingestion.ablation [--models bge-small-v1.5 ...]
"""

import argparse
import logging
import time

from common.settings import get_settings
from ingestion import bundle
from ingestion.embed import ABLATION, Embedder

log = logging.getLogger("ablation")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", nargs="*", default=[m.key for m in ABLATION])
    parser.add_argument("--processes", type=int, default=8)
    parser.add_argument("--threads", type=int, default=4)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    settings = get_settings()
    root = settings.data_dir / "index_bundle"
    b = bundle.read(root)
    for m in [m for m in ABLATION if m.key in args.models]:
        t = time.perf_counter()
        emb = Embedder(settings.data_dir / "cache", processes=args.processes, threads=args.threads, model=m)
        log.info(
            "%s: %d chunks on %d processes x %d threads",
            m.key,
            len(b.embed_texts),
            emb.processes,
            emb.threads,
        )
        matrix = emb.dense_docs(b.embed_texts)
        info = {
            "model": m.model,
            "doc_prefix": m.doc_prefix,
            "query_prefix": m.query_prefix,
            "ablation": True,
        }
        bundle.add_dense(root, m.key, info, matrix)
        dt = time.perf_counter() - t
        log.info("%s done in %.0fs (%.1f chunks/s)", m.key, dt, len(b.embed_texts) / dt)


if __name__ == "__main__":
    main()
