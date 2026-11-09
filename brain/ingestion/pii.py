"""PII masking for document chunks (Presidio + spaCy NER + checksum-validated regexes).

Masked text uses stable placeholders per document, e.g. ⟨PERSON_1⟩, ⟨IBAN_CODE_2⟩:
the same value always maps to the same placeholder within a document, so the
masked text stays readable and coreference survives. Originals are stored only
encrypted in Postgres (chunks.text_original_enc), never in Qdrant or logs.

NER (PERSON) runs only where personal names are expected (contracts, Acme docs);
on regulations it mostly hits role titles and hurts retrieval. Structured PII
(email, phone, IBAN, cards, SSN, EIN) is masked everywhere.
"""

from dataclasses import dataclass, field
from functools import lru_cache

from presidio_analyzer import AnalyzerEngine, Pattern, PatternRecognizer, RecognizerResult
from presidio_analyzer.nlp_engine import NlpEngineProvider

# IP_ADDRESS deliberately excluded: on CUAD every "IP" hit (230/230) was a dotted clause number like
# 5.3.1.1 — masking those breaks exact clause lookups (the reason BM25 is in the hybrid).
STRUCTURED = ["EMAIL_ADDRESS", "PHONE_NUMBER", "IBAN_CODE", "CREDIT_CARD", "US_SSN", "US_EIN"]
WITH_NER = [*STRUCTURED, "PERSON"]
SCORE_THRESHOLD = 0.5
# Bump when masking behaviour changes (entity list, recognizers, placeholder format): invalidates caches.
PII_VERSION = "v2-no-ip"


@dataclass
class Masker:
    """Per-document placeholder registry."""

    ner: bool
    # Exact spans never to mask, e.g. tokens of known vendor company names ("Robinson, Cook and
    # Jenkins" is a company, not three people). Company names are not personal data.
    allow_list: frozenset[str] = frozenset()
    _ids: dict[tuple[str, str], str] = field(default_factory=dict)
    _counts: dict[str, int] = field(default_factory=dict)

    def placeholder(self, entity: str, value: str) -> str:
        key = (entity, value.strip().lower())
        if key not in self._ids:
            self._counts[entity] = self._counts.get(entity, 0) + 1
            self._ids[key] = f"⟨{entity}_{self._counts[entity]}⟩"
        return self._ids[key]

    def mask(self, text: str) -> tuple[str, list[str]]:
        """Returns (masked_text, entity types found)."""
        results = _analyzer().analyze(
            text=text,
            language="en",
            entities=WITH_NER if self.ner else STRUCTURED,
            score_threshold=SCORE_THRESHOLD,
            allow_list=list(self.allow_list),
        )
        spans = _non_overlapping(results)
        out, last = [], 0
        for r in spans:
            out.append(text[last : r.start])
            out.append(self.placeholder(r.entity_type, text[r.start : r.end]))
            last = r.end
        out.append(text[last:])
        return "".join(out), sorted({r.entity_type for r in spans})


def _non_overlapping(results: list[RecognizerResult]) -> list[RecognizerResult]:
    """Keep the highest-scoring (then longest) span where recognizers overlap."""
    chosen: list[RecognizerResult] = []
    for r in sorted(results, key=lambda r: (-r.score, -(r.end - r.start))):
        if all(r.end <= c.start or r.start >= c.end for c in chosen):
            chosen.append(r)
    return sorted(chosen, key=lambda r: r.start)


@lru_cache
def _analyzer() -> AnalyzerEngine:
    nlp = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_lg"}],
        }
    ).create_engine()
    engine = AnalyzerEngine(nlp_engine=nlp, supported_languages=["en"])
    # US Employer Identification Number: 12-3456789. The bare pattern is weak (0.3) and only
    # crosses the threshold when context words appear nearby, which avoids masking clause numbers.
    engine.registry.add_recognizer(
        PatternRecognizer(
            supported_entity="US_EIN",
            patterns=[Pattern("ein", r"\b\d{2}-\d{7}\b", 0.3)],
            context=["ein", "tin", "tax", "taxpayer", "employer identification", "identification number"],
        )
    )
    return engine
