"""Turn each source document into markdown with headings (input to structure-aware chunking).

PDFs: docling (layout model) — keeps clause headings and lists and drops running
page headers; measured 2.5 s/page vs pypdf's 57 ms/page but far cleaner output.
Pages are split into ranges and parsed in parallel processes.
If docling fails on a range, that range falls back to pypdf text.
Parsed markdown is cached by the PDF's sha256, so re-ingestion is cheap.
"""

import logging
import os
import re
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from pypdf import PdfReader

from common.resources import capped_workers
from ingestion.sources import SourceDoc

log = logging.getLogger(__name__)

PARSER_VERSION = "docling-v1"
PAGES_PER_TASK = 40
THREADS_PER_WORKER = 4

_converter = None


def _init_worker() -> None:
    global _converter
    from docling.datamodel.accelerator_options import AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import PdfPipelineOptions
    from docling.document_converter import DocumentConverter, PdfFormatOption

    # Born-digital PDFs: no OCR needed. Table structure on: the regulations contain threshold tables.
    opts = PdfPipelineOptions(
        do_ocr=False,
        do_table_structure=True,
        accelerator_options=AcceleratorOptions(num_threads=THREADS_PER_WORKER, device="cpu"),
    )
    _converter = DocumentConverter(format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)})


def _parse_range(args: tuple[str, int, int]) -> tuple[int, str, str]:
    path, first, last = args
    try:
        assert _converter is not None
        md = _converter.convert(path, page_range=(first, last)).document.export_to_markdown()
        return first, md, "docling"
    except Exception as exc:
        log.warning("docling failed on %s pages %d-%d (%s); falling back to pypdf", path, first, last, exc)
        reader = PdfReader(path)
        text = "\n\n".join(reader.pages[i].extract_text() or "" for i in range(first - 1, last))
        return first, text, "pypdf"


def parse_pdf(doc: SourceDoc, cache_dir: Path, workers: int) -> str:
    assert doc.path is not None
    cached = cache_dir / f"{doc.sha256}-{PARSER_VERSION}.md"
    if cached.exists():
        return cached.read_text()
    n_pages = len(PdfReader(doc.path).pages)
    tasks = [
        (str(doc.path), p, min(p + PAGES_PER_TASK - 1, n_pages))
        for p in range(1, n_pages + 1, PAGES_PER_TASK)
    ]
    workers = capped_workers(min(workers, len(tasks)), gb_per_worker=2.5)
    log.info("parsing %s: %d pages in %d tasks on %d workers", doc.id, n_pages, len(tasks), workers)
    with ProcessPoolExecutor(max_workers=workers, initializer=_init_worker) as pool:
        parts = sorted(pool.map(_parse_range, tasks))
    fallbacks = sum(1 for _, _, how in parts if how == "pypdf")
    if fallbacks:
        log.warning("%s: %d/%d page ranges used the pypdf fallback", doc.id, fallbacks, len(parts))
    md = "\n\n".join(text for _, text, _ in parts)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached.write_text(md)
    return md


# CUAD texts are plain text from SEC filings; promote clause-like lines to headings.
_CLAUSE_HEADING = re.compile(
    r"^\s*((ARTICLE|Article|SECTION|Section)\s+[\dIVXLC]+[.:]?(\s+.{0,80})?"
    r"|\d{1,2}(\.\d{1,2}){0,2}\.?\s+[A-Z][A-Za-z ,&/'()-]{2,80}"
    r"|[A-Z][A-Z ,&/'()-]{3,60})\s*$"
)


def text_to_markdown(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if stripped and len(stripped) < 100 and _CLAUSE_HEADING.match(stripped):
            lines.append(f"## {stripped}")
        else:
            lines.append(line)
    return "\n".join(lines)


def to_markdown(doc: SourceDoc, cache_dir: Path, workers: int = os.cpu_count() or 4) -> str:
    if doc.kind == "pdf":
        return parse_pdf(doc, cache_dir, workers)
    if doc.kind == "markdown":
        assert doc.path is not None
        return doc.path.read_text()
    assert doc.text is not None
    return text_to_markdown(doc.text)
