import math

from evals.metrics import bootstrap_ci, hit_at, ndcg_at, recall_at, reciprocal_rank, regressed, summarize

RANKED = ["a", "x", "b", "y", "z"]
GOLD = {"a", "b", "c"}


def test_rank_metrics_known_values() -> None:
    assert recall_at(RANKED, GOLD, 5) == 2 / 3
    assert recall_at(RANKED, GOLD, 1) == 1 / 3
    assert hit_at(RANKED, {"b"}, 2) == 0.0 and hit_at(RANKED, {"b"}, 3) == 1.0
    assert reciprocal_rank(RANKED, {"b"}) == 1 / 3
    assert reciprocal_rank(RANKED, {"q"}) == 0.0
    dcg = 1 / math.log2(2) + 1 / math.log2(4)
    ideal = 1 / math.log2(2) + 1 / math.log2(3) + 1 / math.log2(4)
    assert math.isclose(ndcg_at(RANKED, GOLD, 5), dcg / ideal)


def test_bootstrap_ci_contains_mean_and_is_deterministic() -> None:
    values = [0.0, 1.0] * 50
    lo, hi = bootstrap_ci(values)
    assert lo < 0.5 < hi
    assert bootstrap_ci(values) == (lo, hi)


def test_regression_needs_both_tolerance_and_significance() -> None:
    noisy = summarize([0.9, 1.0, 0.8, 1.0, 0.95, 0.85])  # wide CI
    assert not regressed(0.93, noisy, tolerance=0.02), "inside the CI -> noise, not a regression"
    tight = {"mean": 0.80, "ci_low": 0.79, "ci_high": 0.81, "n": 1000}
    assert regressed(0.93, tight, tolerance=0.02)
    assert not regressed(0.815, tight, tolerance=0.02), "significant but below tolerance"
    # Lower-is-better metrics (latency, cost, attack success rate).
    assert regressed(0.10, {"mean": 0.30, "ci_low": 0.28, "ci_high": 0.32}, 0.05, higher_is_better=False)
