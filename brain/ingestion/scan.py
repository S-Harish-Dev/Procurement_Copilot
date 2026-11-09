"""Ingestion-time prompt-injection scan for document chunks (indirect injection defence).

Layer 1 — versioned regex heuristics from prompts/injection_patterns_v1.json, run on
normalized text and on any decoded base64/hex blobs inside it.
Layer 2 — Prompt Guard 2 (86M) classifier score, when the model is available
(gated on Hugging Face; see ingestion/promptguard.py).

A flagged chunk is still indexed (the runtime relevance gate and spotlighting
handle it) unless it is high-risk, in which case it is quarantined: kept in
Postgres for review, never upserted to Qdrant.
"""

import base64
import binascii
import json
import re
from dataclasses import dataclass, field
from functools import lru_cache

from common.settings import REPO_ROOT
from ingestion.normalize import normalize

PATTERNS_FILE = REPO_ROOT / "prompts" / "injection_patterns_v1.json"
PG2_FLAG = 0.5  # Prompt Guard 2 score that flags a chunk
PG2_QUARANTINE = 0.9  # ...and that quarantines it


@dataclass(frozen=True)
class Pattern:
    id: str
    severity: str
    regex: re.Pattern[str]


@dataclass
class ScanResult:
    hits: list[str] = field(default_factory=list)  # pattern ids ("b64:" prefix when found in a decoded blob)
    high: bool = False
    pg2_score: float | None = None

    @property
    def flagged(self) -> bool:
        return bool(self.hits) or (self.pg2_score is not None and self.pg2_score >= PG2_FLAG)

    @property
    def quarantine(self) -> bool:
        return self.high or (self.pg2_score is not None and self.pg2_score >= PG2_QUARANTINE)


@lru_cache
def _load() -> tuple[list[Pattern], re.Pattern[str], re.Pattern[str]]:
    spec = json.loads(PATTERNS_FILE.read_text())
    patterns = [
        Pattern(p["id"], p["severity"], re.compile(p["pattern"], re.IGNORECASE | re.DOTALL))
        for p in spec["patterns"]
    ]
    return patterns, re.compile(spec["encoded_blob"]["base64"]), re.compile(spec["encoded_blob"]["hex"])


def _decoded_blobs(text: str) -> list[str]:
    _, b64, hexre = _load()
    out = []
    for m in b64.finditer(text):
        try:
            out.append(
                base64.b64decode(m.group(0) + "=" * (-len(m.group(0)) % 4), validate=True).decode("utf-8")
            )
        except binascii.Error, UnicodeDecodeError:
            continue
    for m in hexre.finditer(text):
        try:
            out.append(bytes.fromhex(m.group(0)).decode("utf-8"))
        except ValueError, UnicodeDecodeError:
            continue
    return out


def regex_scan(text: str) -> ScanResult:
    patterns, _, _ = _load()
    result = ScanResult()
    norm = normalize(text)
    targets = [("", norm)] + [("b64:", normalize(d)) for d in _decoded_blobs(norm)]
    for prefix, target in targets:
        for p in patterns:
            if p.regex.search(target):
                result.hits.append(prefix + p.id)
                result.high |= p.severity == "high"
    return result
