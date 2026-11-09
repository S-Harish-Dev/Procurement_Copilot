"""Text normalization applied before any security check (mirrors the Rust guard crate).

Attackers hide instructions with zero-width characters, bidi overrides and
compatibility forms ("ｉｇｎｏｒｅ"). NFKC folds compatibility forms; the rest is stripped.
"""

import re
import unicodedata

# Zero-width, joiners, bidi embeddings/overrides/isolates, word joiner/invisible operators, BOM, soft hyphen.
_INVISIBLE = re.compile("[­​-‏‪-‮⁠-⁤⁦-⁩﻿]")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACES = re.compile(r"[ \t]+")


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    text = _INVISIBLE.sub("", text)
    text = _CONTROL.sub("", text)
    return _SPACES.sub(" ", text)
