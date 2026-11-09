"""Retrieval metrics and bootstrap confidence intervals.

All metrics use binary relevance (a chunk is gold or not). Aggregates are means over cases with a
95% percentile-bootstrap CI (seeded, so the same results give the same interval).
"""

import math
import random
from collections.abc import Sequence


def recall_at(ranked: Sequence[str], gold: set[str], k: int) -> float:
    return len(gold.intersection(ranked[:k])) / len(gold) if gold else 0.0


def hit_at(ranked: Sequence[str], gold: set[str], k: int) -> float:
    return 1.0 if gold.intersection(ranked[:k]) else 0.0


def reciprocal_rank(ranked: Sequence[str], gold: set[str]) -> float:
    for i, c in enumerate(ranked, start=1):
        if c in gold:
            return 1.0 / i
    return 0.0


def ndcg_at(ranked: Sequence[str], gold: set[str], k: int) -> float:
    dcg = sum(1.0 / math.log2(i + 1) for i, c in enumerate(ranked[:k], start=1) if c in gold)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(gold), k) + 1))
    return dcg / ideal if ideal else 0.0


def bootstrap_ci(
    values: Sequence[float], n: int = 1000, seed: int = 7, alpha: float = 0.05
) -> tuple[float, float]:
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = sorted(sum(rng.choices(values, k=len(values))) / len(values) for _ in range(n))
    lo = means[int(n * alpha / 2)]
    hi = means[min(n - 1, int(n * (1 - alpha / 2)))]
    return (lo, hi)


def summarize(values: Sequence[float]) -> dict[str, float]:
    mean = sum(values) / len(values) if values else 0.0
    lo, hi = bootstrap_ci(values)
    return {"mean": round(mean, 4), "ci_low": round(lo, 4), "ci_high": round(hi, 4), "n": len(values)}


def percentile(values: Sequence[float], p: float) -> float:
    if not values:
        return 0.0
    v = sorted(values)
    return v[min(len(v) - 1, round(p / 100 * (len(v) - 1)))]


def regressed(
    baseline: float, current: dict[str, float], tolerance: float, higher_is_better: bool = True
) -> bool:
    """Fail only if the drop exceeds the tolerance AND the baseline is outside the current 95% CI.

    The CI condition stops noise on a few hundred cases from failing builds; the tolerance stops a
    statistically significant but negligible change from failing them.
    """
    if higher_is_better:
        return (baseline - current["mean"]) > tolerance and baseline > current["ci_high"]
    return (current["mean"] - baseline) > tolerance and baseline < current["ci_low"]
