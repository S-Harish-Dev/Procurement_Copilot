"""Structure-aware chunking.

1. Split markdown into sections at headings; keep a heading stack -> `section_path`
   (e.g. "PART 9 … › Subpart 9.3 … › 9.302 General.").
2. Inside a section, pack paragraphs up to TARGET_TOKENS, measured with the
   embedding model's own tokenizer (so "400 tokens" means what the model sees).
3. Consecutive chunks of a section overlap by ~15% (whole trailing paragraphs or
   sentences) so a clause cut at a boundary is still retrievable from either side.
Chunks never cross a section boundary: a citation then points at one clause.
"""

import re
from dataclasses import dataclass
from functools import lru_cache

from tokenizers import Tokenizer

CHUNKER_VERSION = "v1"
TARGET_TOKENS = 400
OVERLAP_TOKENS = 60  # 15% of TARGET_TOKENS
MIN_CHUNK_TOKENS = 20  # drop fragments like stray page numbers
TOKENIZER_MODEL = "nomic-ai/nomic-embed-text-v1.5"

_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_PART = re.compile(r"^part\s+\d+\b", re.I)
_SUBPART = re.compile(r"^subpart\s+\d+\.\d+\b", re.I)
_NUMBERED = re.compile(r"^(?:section\s+|article\s+|clause\s+)?(\d+(?:\.\d+)*)[.)]?(?:-\d+)?\s", re.I)
_SENTENCE = re.compile(r"(?<=[.;:!?])\s+(?=[A-Z(\"'])")


@dataclass(frozen=True)
class Chunk:
    ord: int
    section_path: str
    text: str
    token_count: int


@lru_cache
def _tokenizer() -> Tokenizer:
    return Tokenizer.from_pretrained(TOKENIZER_MODEL)


def count_tokens(text: str) -> int:
    return len(_tokenizer().encode(text, add_special_tokens=False).ids)


def _heading_level(title: str, md_level: int) -> int:
    """Infer depth from numbering where present (more reliable than docling's flat '##')."""
    if _PART.match(title):
        return 1
    if _SUBPART.match(title):
        return 2
    m = _NUMBERED.match(title)
    if m:
        return 2 + m.group(1).count(".")  # "9" -> 2, "9.302" -> 3, "4.2.1" -> 4
    return md_level


def _number(title: str) -> str | None:
    m = _PART.match(title) or _SUBPART.match(title) or _NUMBERED.match(title)
    if not m:
        return None
    return re.search(r"\d+(?:\.\d+)*", m.group(0)).group(0)  # type: ignore[union-attr]


def _contains(ancestor: str, num: str) -> bool:
    """'9' contains '9.302'; FAR subpart '9.3' contains section '9.302'; '9.3' does not contain '9.400'."""
    if "." not in ancestor:
        return num == ancestor or num.startswith(ancestor + ".")
    return num.startswith(ancestor)


def _sections(markdown: str) -> list[tuple[str, str]]:
    stack: list[tuple[int, str]] = []
    out: list[tuple[str, list[str]]] = [("", [])]
    for line in markdown.splitlines():
        m = _HEADING.match(line)
        if m:
            title = m.group(2).strip()
            level = _heading_level(title, len(m.group(1)))
            while stack and stack[-1][0] >= level:
                stack.pop()
            # PDFs sometimes emit a subpart title after its first section; drop numbered
            # ancestors that cannot contain this heading's number.
            num = _number(title)
            if num:
                while stack and (anc := _number(stack[-1][1])) and not _contains(anc, num):
                    stack.pop()
            stack.append((level, title))
            out.append((" › ".join(t for _, t in stack), []))
        else:
            out[-1][1].append(line)
    return [(path, "\n".join(lines).strip()) for path, lines in out if "\n".join(lines).strip()]


def _units(body: str) -> list[str]:
    """Paragraphs; any paragraph longer than the target is split into sentences, then hard-split."""
    units: list[str] = []
    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para:
            continue
        if count_tokens(para) <= TARGET_TOKENS:
            units.append(para)
            continue
        for sent in _SENTENCE.split(para):
            if count_tokens(sent) <= TARGET_TOKENS:
                units.append(sent)
            else:
                ids = _tokenizer().encode(sent, add_special_tokens=False).ids
                for i in range(0, len(ids), TARGET_TOKENS):
                    units.append(_tokenizer().decode(ids[i : i + TARGET_TOKENS]))
    return units


def chunk_markdown(markdown: str) -> list[Chunk]:
    chunks: list[Chunk] = []
    for path, body in _sections(markdown):
        units = [(u, count_tokens(u)) for u in _units(body)]
        current: list[tuple[str, int]] = []
        size = 0
        for unit, n in units:
            if current and size + n > TARGET_TOKENS:
                chunks.append(_make(len(chunks), path, current))
                # Carry trailing units (up to OVERLAP_TOKENS) into the next chunk.
                carry: list[tuple[str, int]] = []
                carried = 0
                for u in reversed(current):
                    if carried + u[1] > OVERLAP_TOKENS:
                        break
                    carry.insert(0, u)
                    carried += u[1]
                current, size = carry, carried
            current.append((unit, n))
            size += n
        if current and size >= MIN_CHUNK_TOKENS:
            chunks.append(_make(len(chunks), path, current))
    return chunks


def _make(ord_: int, path: str, units: list[tuple[str, int]]) -> Chunk:
    text = "\n\n".join(u for u, _ in units)
    return Chunk(ord=ord_, section_path=path, text=text, token_count=count_tokens(text))
