"""Enumerate every document to ingest, with its access-control metadata."""

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

from common.settings import Settings

# CUAD contract types that look like vendor/procurement agreements.
CUAD_TYPES = (
    "supply",
    "service",
    "outsourcing",
    "maintenance",
    "manufacturing",
    "hosting",
    "license",
    "distributor",
    "reseller",
    "transportation",
    "consulting",
    "development",
)
CUAD_SUBSET = 100


@dataclass(frozen=True)
class SourceDoc:
    id: str
    source: str  # far | worldbank | un | cuad | acme
    title: str
    kind: str  # pdf | markdown | text
    department: str
    classification: str
    effective_date: str | None
    ner: bool  # run person-name NER during PII masking
    sha256: str
    path: Path | None = None  # pdf / markdown
    text: str | None = None  # inline text (CUAD)
    poisoned: bool = False  # ground truth for planted injection docs


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _slug(s: str, n: int = 48) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:n]


def public_pdfs(settings: Settings) -> list[SourceDoc]:
    manifest = json.loads((settings.data_dir / "manifest.json").read_text())
    docs = []
    for src in manifest["sources"]:
        if not src["file"].endswith(".pdf"):
            continue
        path = settings.data_dir / "raw" / src["id"] / src["file"]
        docs.append(
            SourceDoc(
                id=f"{src['id']}",
                source=src["id"],
                title=src["title"],
                kind="pdf",
                department=src["department"],
                classification=src["classification"],
                effective_date=None,
                ner=False,
                sha256=src["sha256"],
                path=path,
            )
        )
    return docs


def cuad_contracts(settings: Settings) -> list[SourceDoc]:
    """Deterministic subset: matching types, ordered by hash of title (spreads across types)."""
    with zipfile.ZipFile(settings.data_dir / "raw" / "cuad" / "data.zip") as z:
        data = json.loads(z.read("CUADv1.json"))["data"]
    candidates = [d for d in data if any(t in d["title"].lower() for t in CUAD_TYPES)]
    candidates.sort(key=lambda d: _sha(d["title"].encode()))
    docs = []
    for d in candidates[:CUAD_SUBSET]:
        text = d["paragraphs"][0]["context"]
        docs.append(
            SourceDoc(
                id=f"cuad-{_slug(d['title'])}-{_sha(d['title'].encode())[:6]}",
                source="cuad",
                title=d["title"],
                kind="text",
                department="all",
                classification="public",
                effective_date=None,
                ner=True,
                sha256=_sha(text.encode()),
                text=text,
            )
        )
    return docs


def acme_docs(settings: Settings) -> list[SourceDoc]:
    base = settings.data_dir / "synthetic"
    index = json.loads((base / "docs" / "index.json").read_text())
    docs = []
    for e in index:
        path = base / e["path"]
        docs.append(
            SourceDoc(
                id=e["id"],
                source="acme",
                title=e["title"],
                kind="markdown",
                department=e["department"],
                classification=e["classification"],
                effective_date=e.get("effective_date"),
                ner=True,
                sha256=_sha(path.read_bytes()),
                path=path,
                poisoned=e.get("poisoned", False),
            )
        )
    return docs


def all_documents(settings: Settings) -> list[SourceDoc]:
    return public_pdfs(settings) + cuad_contracts(settings) + acme_docs(settings)


def vendor_name_tokens(settings: Settings) -> frozenset[str]:
    """Company names (and their parts) that must not be masked as PERSON."""
    vendors = json.loads((settings.data_dir / "synthetic" / "vendors.json").read_text())
    tokens: set[str] = set()
    for v in vendors:
        tokens.add(v["name"])
        tokens.update(t for t in re.split(r",?\s+(?:and\s+)?|,\s*", v["name"]) if t)
    return frozenset(tokens)
