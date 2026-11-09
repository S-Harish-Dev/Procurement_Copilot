"""Index LOAD (light, runs anywhere): verify the bundle, then write Postgres + Qdrant. No model inference.

uv run python -m ingestion.load                       # Postgres + Qdrant server from .env
uv run python -m ingestion.load --verify-only         # checksums + consistency only
uv run python -m ingestion.load --qdrant-path DIR --skip-postgres   # embedded Qdrant (tests)
"""

import argparse
import logging
import sys
import time
from collections import defaultdict

from qdrant_client import QdrantClient

from common.settings import get_settings
from ingestion import bundle, store
from ingestion.embed import SparseVec

log = logging.getLogger("load")


def check(b: bundle.Bundle) -> list[str]:
    """Consistency checks beyond checksums. Returns problems (empty = good)."""
    problems = []
    n = len(b.chunks)
    doc_ids = {d["id"] for d in b.documents}
    if len(b.embed_texts) != n or len(b.sparse) != n:
        problems.append("chunks / embed_texts / sparse lengths differ")
    for key, info in b.manifest["dense_models"].items():
        m = b.dense(key)
        if m.shape != (n, info["dim"]):
            problems.append(f"dense/{key} shape {m.shape} != ({n}, {info['dim']})")
    if missing := {c.doc_id for c in b.chunks} - doc_ids:
        problems.append(f"chunks reference unknown documents: {sorted(missing)[:5]}")
    if len({c.chunk_id for c in b.chunks}) != n:
        problems.append("duplicate chunk ids")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--skip-postgres", action="store_true")
    parser.add_argument("--qdrant-path", help="use embedded Qdrant at this directory instead of QDRANT_URL")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per upsert batch is noise

    settings = get_settings()
    t = time.perf_counter()
    b = bundle.read(settings.data_dir / "index_bundle")
    if problems := check(b):
        for p in problems:
            log.error(p)
        return 1
    log.info(
        "bundle ok: %d documents, %d chunks, dense models %s (verified in %.1fs)",
        len(b.documents),
        len(b.chunks),
        list(b.manifest["dense_models"]),
        time.perf_counter() - t,
    )
    if args.verify_only:
        return 0

    primary = b.manifest["primary_dense"]
    dense = b.dense(primary)
    sparse = [SparseVec(i, v) for i, v in b.sparse]
    by_doc: dict[str, list[int]] = defaultdict(list)
    for i, c in enumerate(b.chunks):
        by_doc[c.doc_id].append(i)

    client = (
        QdrantClient(path=args.qdrant_path)
        if args.qdrant_path
        else QdrantClient(url=settings.qdrant_url, timeout=120)
    )
    store.ensure_collection(client)

    if args.skip_postgres:
        version = 1
        conn = None
    else:
        import psycopg

        conn = psycopg.connect(settings.database_url)
        version = store.new_index_version(conn, f"bundle {b.manifest['created_at']} {b.manifest['versions']}")
        conn.commit()

    upserted = 0
    for doc in b.documents:
        idx = by_doc[doc["id"]]
        recs = [b.chunks[i] for i in idx]
        if conn is not None:
            with conn.transaction():
                store.write_document(conn, doc, version)
                store.write_chunks(conn, recs, version)
        upserted += store.upsert_points(client, doc, recs, dense[idx], [sparse[i] for i in idx], version)
    if conn is not None:
        store.activate(conn, client, version)
        conn.close()
    log.info(
        "loaded index_version %d: %d points upserted (%d quarantined chunks kept out of Qdrant)",
        version,
        upserted,
        sum(c.quarantined for c in b.chunks),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
