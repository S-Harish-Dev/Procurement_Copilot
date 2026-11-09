"""Prompt Guard 2 (86M) classifier for ingestion-time scanning.

The model is gated on Hugging Face under the Llama license: it loads only when
HF_TOKEN is set and the license was accepted. Without it the scanner runs
regex-only and the ingest report says so — it never silently claims coverage.
(The Rust gateway uses an ONNX export of the same model in M6.)
"""

import logging
from functools import lru_cache

import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

from common.settings import get_settings

log = logging.getLogger(__name__)

MODEL_ID = "meta-llama/Llama-Prompt-Guard-2-86M"
MAX_TOKENS = 512
STRIDE = 384  # sliding windows overlap so an instruction split across a boundary is still seen


class PromptGuard:
    def __init__(self, token: str) -> None:
        self.tok = AutoTokenizer.from_pretrained(MODEL_ID, token=token)
        self.model = AutoModelForSequenceClassification.from_pretrained(MODEL_ID, token=token).eval()
        # Label 1 = malicious for Prompt Guard 2.
        self.malicious = 1

    def score(self, texts: list[str]) -> list[float]:
        """Max malicious probability over sliding 512-token windows of each text."""
        scores = []
        # A `with` block, not a decorator: torch is optional, and an untyped decorator breaks strict mypy.
        with torch.inference_mode():
            for text in texts:
                ids = self.tok(text, add_special_tokens=False)["input_ids"]
                windows = [ids[i : i + MAX_TOKENS - 2] for i in range(0, max(len(ids), 1), STRIDE)] or [[]]
                batch = self.tok.pad(
                    {"input_ids": [self.tok.build_inputs_with_special_tokens(w) for w in windows]},
                    return_tensors="pt",
                )
                probs = torch.softmax(self.model(**batch).logits, dim=-1)[:, self.malicious]
                scores.append(float(probs.max()))
        return scores


@lru_cache
def load() -> PromptGuard | None:
    token = get_settings().hf_token.get_secret_value()
    if not token:
        log.warning("HF_TOKEN not set: Prompt Guard 2 disabled, injection scan is regex-only")
        return None
    try:
        return PromptGuard(token)
    except Exception as exc:
        log.warning("Prompt Guard 2 unavailable (%s): injection scan is regex-only", exc)
        return None
