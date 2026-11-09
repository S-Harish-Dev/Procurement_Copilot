"""Index BUILD (heavy, run on the build machine): parse -> chunk -> PII-mask -> injection-scan -> embed.

    uv run --group ingest python -m ingestion.pipeline                 # all sources
    uv run --group ingest python -m ingestion.pipeline --sources acme  # subset (writes a partial bundle)

Output is the index bundle (data/index_bundle/, see ingestion/bundle.py) plus
data/build/ingest_report.json. Loading the bundle into Postgres + Qdrant is a separate, light step
(`ingestion.load`) so a laptop never has to parse PDFs, run spaCy or embed the corpus.
"""

import argparse
import hashlib
import json
import logging
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict

from common.resources import capped_workers
from common.settings import get_settings
from ingestion import bundle, promptguard
from ingestion.chunk import CHUNKER_VERSION, chunk_markdown
from ingestion.embed import EMBED_VERSION, PRIMARY, SPARSE_MODEL, Embedder, contextual_text
from ingestion.parse import PARSER_VERSION, to_markdown
from ingestion.pii import PII_VERSION, Masker
from ingestion.scan import PATTERNS_FILE, regex_scan
from ingestion.sources import SourceDoc, all_documents, vendor_name_tokens
from ingestion.store import ChunkRecord

log = logging.getLogger("ingest")

_allow_list: frozenset[str] = frozenset()


def _init_mask_worker(allow: frozenset[str]) -> None:
    global _allow_list
    _allow_list = allow


def _chunk_mask_scan(args: tuple[SourceDoc, str]) -> tuple[str, list[ChunkRecord], Counter[str]]:
    """CPU-heavy per-document work (spaCy NER), run in a process pool."""
    doc, markdown = args
    masker = Masker(ner=doc.ner, allow_list=_allow_list)
    entities: Counter[str] = Counter()
    records = []
    for c in chunk_markdown(markdown):
        masked, found = masker.mask(c.text)
        entities.update(found)
        scan = regex_scan(c.text)
        records.append(
            ChunkRecord(
                chunk_id=f"{doc.id}#{c.ord}",
                doc_id=doc.id,
                ord=c.ord,
                section_path=c.section_path,
                text_masked=masked,
                text_original=c.text,
                token_count=c.token_count,
                injection_flag=scan.flagged,
                injection_score=None,
                quarantined=scan.quarantine,
            )
        )
        if scan.hits:
            log.info("injection heuristics hit %s#%d: %s", doc.id, c.ord, scan.hits)
    return doc.id, records, entities


def _stage_key(doc: SourceDoc, allow: frozenset[str]) -> str:
    """Cache key for a document's chunk/mask/scan output: changes whenever any input to that stage does."""
    parts = [
        doc.sha256,
        CHUNKER_VERSION,
        PII_VERSION,
        str(doc.ner),
        hashlib.sha256("\n".join(sorted(allow)).encode()).hexdigest(),
        hashlib.sha256(PATTERNS_FILE.read_bytes()).hexdigest(),
    ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _doc_row(doc: SourceDoc) -> dict[str, object]:
    return {
        "id": doc.id,
        "source": doc.source,
        "title": doc.title,
        "path": str(doc.path.relative_to(get_settings().data_dir)) if doc.path else f"cuad:{doc.title}",
        "sha256": doc.sha256,
        "department": doc.department,
        "classification": doc.classification,
        "effective_date": doc.effective_date,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sources", nargs="*", help="only these sources (far, worldbank, un, cuad, acme)")
    # Masking workers hold spaCy en_core_web_lg + Presidio (~1.5 GB each); embedding workers ~3.7 GB.
    # Both pools are capped by free memory at start-up (shared machine).
    parser.add_argument("--workers", type=int, default=16, help="chunk/mask/scan processes")
    parser.add_argument("--embed-processes", type=int, default=8)
    parser.add_argument("--embed-threads", type=int, default=4)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    settings = get_settings()
    cache = settings.data_dir / "cache"
    timings: dict[str, float] = {}
    docs = [d for d in all_documents(settings) if not args.sources or d.source in args.sources]
    log.info("%d documents", len(docs))

    t = time.perf_counter()
    markdown = {d.id: to_markdown(d, cache / "parsed", workers=args.workers) for d in docs}
    timings["parse_s"] = time.perf_counter() - t

    t = time.perf_counter()
    by_id = {d.id: d for d in docs}
    records: dict[str, list[ChunkRecord]] = {}
    entities: Counter[str] = Counter()
    allow = vendor_name_tokens(settings)
    stage_cache = cache / "chunks"
    stage_cache.mkdir(parents=True, exist_ok=True)
    keys = {d.id: _stage_key(d, allow) for d in docs}
    todo = []
    for d in docs:
        hit = stage_cache / f"{keys[d.id]}.json"
        if hit.exists():
            cached = json.loads(hit.read_text())
            records[d.id] = [ChunkRecord(**r) for r in cached["records"]]
            entities.update(cached["entities"])
        else:
            todo.append(d)
    mask_workers = capped_workers(args.workers, gb_per_worker=1.5)
    log.info(
        "chunk/mask/scan: %d cached, %d to do on %d processes", len(docs) - len(todo), len(todo), mask_workers
    )
    if todo:
        with ProcessPoolExecutor(
            max_workers=mask_workers, initializer=_init_mask_worker, initargs=(allow,)
        ) as pool:
            for doc_id, recs, ents in pool.map(_chunk_mask_scan, [(d, markdown[d.id]) for d in todo]):
                records[doc_id] = recs
                entities.update(ents)
                payload = {"records": [asdict(r) for r in recs], "entities": dict(ents)}
                (stage_cache / f"{keys[doc_id]}.json").write_text(json.dumps(payload, ensure_ascii=False))
    timings["chunk_mask_scan_s"] = time.perf_counter() - t

    # Prompt Guard 2 second opinion on every chunk (if available).
    t = time.perf_counter()
    pg2 = promptguard.load()
    if pg2 is not None:
        for recs in records.values():
            for rec, score in zip(recs, pg2.score([r.text_original for r in recs]), strict=True):
                rec.injection_score = score
                rec.injection_flag |= score >= 0.5
                rec.quarantined |= score >= 0.9
    timings["prompt_guard_s"] = time.perf_counter() - t

    t = time.perf_counter()
    embedder = Embedder(cache, processes=args.embed_processes, threads=args.embed_threads)
    all_recs = [r for d in docs for r in records[d.id]]
    texts = [contextual_text(by_id[r.doc_id].title, r.section_path, r.text_masked) for r in all_recs]
    dense = embedder.dense_docs(texts)
    sparse = embedder.sparse_docs(texts)
    timings["embed_s"] = time.perf_counter() - t

    t = time.perf_counter()
    model_info = {
        "model": PRIMARY.model,
        "doc_prefix": PRIMARY.doc_prefix,
        "query_prefix": PRIMARY.query_prefix,
    }
    bundle.write(
        settings.data_dir / "index_bundle",
        documents=[_doc_row(d) for d in docs],
        chunks=all_recs,
        embed_texts=texts,
        dense={PRIMARY.key: (model_info, dense)},
        sparse=[(s.indices, s.values) for s in sparse],
        meta={
            "versions": {"parser": PARSER_VERSION, "chunker": CHUNKER_VERSION, "embed": EMBED_VERSION},
            "primary_dense": PRIMARY.key,
            "sparse_model": SPARSE_MODEL,
            "prompt_guard_2": pg2 is not None,
        },
    )
    timings["bundle_write_s"] = time.perf_counter() - t

    report = _report(docs, records, entities, timings, pg2 is not None)
    report["timings"] = {k: round(v, 1) for k, v in timings.items()}

    out = settings.data_dir / "build" / "ingest_report.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2) + "\n")
    log.info("report: %s", json.dumps(report))


def _report(
    docs: list[SourceDoc],
    records: dict[str, list[ChunkRecord]],
    entities: Counter[str],
    timings: dict[str, float],
    pg2: bool,
) -> dict[str, object]:
    recs = [r for v in records.values() for r in v]
    source_of = {d.id: d.source for d in docs}
    poisoned = [d.id for d in docs if d.poisoned]
    missed = [d for d in poisoned if not any(r.injection_flag for r in records[d])]
    flagged_benign = sorted({r.doc_id for r in recs if r.injection_flag and r.doc_id not in poisoned})
    return {
        "versions": {"parser": PARSER_VERSION, "chunker": CHUNKER_VERSION, "embed": EMBED_VERSION},
        "documents": len(docs),
        "documents_by_source": dict(Counter(d.source for d in docs)),
        "chunks": len(recs),
        "chunks_by_source": dict(Counter(source_of[r.doc_id] for r in recs)),
        "tokens_total": sum(r.token_count for r in recs),
        "pii_entities_docs_with_type": dict(entities),
        "injection": {
            "prompt_guard_2": "enabled" if pg2 else "unavailable (regex-only)",
            "chunks_flagged": sum(r.injection_flag for r in recs),
            "chunks_quarantined": sum(r.quarantined for r in recs),
            "poisoned_docs": len(poisoned),
            "poisoned_docs_missed": missed,
            "benign_docs_flagged": flagged_benign,
        },
    }


if __name__ == "__main__":
    main()
