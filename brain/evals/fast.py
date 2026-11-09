"""`make eval-fast` — no LLM generation, deterministic, runs before every commit.

What it measures:
- Retrieval quality on the production path: every case is sent to the gateway's
  `/admin/eval/retrieve` *as its persona* (real embedder, BM25 port, OPA scope, Qdrant hybrid query).
  recall@20 (pre-gate), hit@5, MRR, nDCG@10 per category, with bootstrap 95% CIs.
- HARD gates (must be exactly 0):
  * forbidden documents retrieved for role-restricted cases;
  * ACL leaks: every returned chunk is re-checked against OPA directly (`read_doc`), independently
    of the Qdrant filter code — a filter bug can't hide itself;
  * OPA policy test failures.
- Regression gates vs evals/baselines/baseline.json (see metrics.regressed).

Writes evals/reports/<run_id>.json and a row in Postgres `eval_runs`. Exit 1 if any gate fails.

    uv run python -m evals.fast [--update-baseline] [--no-sparse] [--no-dense] [--dense-k N] [--top-k N]
"""

import argparse
import json
import os
import subprocess
import sys
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from typing import Any

import httpx

from common.settings import REPO_ROOT, get_settings
from evals.metrics import hit_at, ndcg_at, percentile, recall_at, reciprocal_rank, regressed, summarize

DATASET = REPO_ROOT / "evals" / "datasets" / "qa.jsonl"
BASELINE = REPO_ROOT / "evals" / "baselines" / "baseline.json"
REPORTS = REPO_ROOT / "evals" / "reports"
GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8080")
OPA = os.environ.get("OPA_URL", "http://127.0.0.1:8181")

# Regression tolerances (absolute) per metric; anything not listed is reported but not gated.
TOLERANCE = {"recall@20": 0.02, "hit@5": 0.03, "mrr": 0.03, "ndcg@10": 0.03, "guard_detection": 0.03}
LOWER_IS_BETTER = {"guard_benign_fpr": 0.02}
GUARD_CATEGORIES = {"direct_injection", "prompt_extraction", "jailbreak_roleplay", "canary_extraction"}


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=REPO_ROOT, check=True
        ).stdout.strip()
    except OSError, subprocess.CalledProcessError:
        return "unknown"


class OpaOracle:
    """Independent ACL check: asks OPA whether `persona` may read a (classification, department) doc."""

    def __init__(self, http: httpx.Client, users: dict[str, dict[str, Any]]) -> None:
        self.http, self.users = http, users
        self.cache: dict[tuple[str, str, str], bool] = {}

    def allowed(self, persona: str, classification: str, department: str) -> bool:
        key = (persona, classification, department)
        if key not in self.cache:
            u = self.users[persona]
            body = {
                "input": {
                    "user": {
                        "id": persona,
                        "role": u["role"],
                        "dept": u["department"],
                        "spend_limit": u["spend_limit"],
                        "approval_limit": u["approval_limit"],
                    },
                    "action": "read_doc",
                    "resource": {"type": "document", "classification": classification, "dept": department},
                }
            }
            r = self.http.post(f"{OPA}/v1/data/acme/authz", json=body)
            r.raise_for_status()
            self.cache[key] = bool(r.json()["result"]["allow"])
        return self.cache[key]


def guard_metrics(http: httpx.Client, headers: dict[str, str]) -> dict[str, list[float]]:
    """Local guard layers (regex + classifier) on the red-team guard categories and the benign set."""
    rt = REPO_ROOT / "evals" / "redteam"
    attacks = [json.loads(x) for x in (rt / "cases.jsonl").read_text().splitlines() if x.strip()]
    attacks = [a for a in attacks if a["category"] in GUARD_CATEGORIES]
    benign = [json.loads(x) for x in (rt / "benign.jsonl").read_text().splitlines() if x.strip()]

    def blocked(text: str) -> bool:
        r = http.post(f"{GATEWAY}/admin/eval/guard", headers=headers, json={"text": text})
        r.raise_for_status()
        return bool(r.json()["blocked"])

    with ThreadPoolExecutor(4) as pool:
        det = list(pool.map(lambda a: 1.0 if blocked(a["prompt"]) else 0.0, attacks))
        fp = list(pool.map(lambda b: 1.0 if blocked(b["prompt"]) else 0.0, benign))
    return {"detected": det, "false_positive": fp}


def opa_tests() -> dict[str, Any]:
    p = subprocess.run(
        ["opa", "test", "policies/", "--format", "json"], capture_output=True, text=True, cwd=REPO_ROOT
    )
    results = json.loads(p.stdout or "[]")
    failed = [r["name"] for r in results if r.get("fail") or r.get("error")]
    return {"passed": len(results) - len(failed), "failed": len(failed), "failures": failed}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--update-baseline", action="store_true", help="write this run as the new baseline")
    ap.add_argument("--no-dense", action="store_true")
    ap.add_argument("--no-sparse", action="store_true")
    ap.add_argument("--dense-k", type=int, default=40)
    ap.add_argument("--sparse-k", type=int, default=40)
    ap.add_argument("--top-k", type=int, default=20)
    ap.add_argument(
        "--label", default="", help="free-text label stored with the report (e.g. an ablation name)"
    )
    args = ap.parse_args()

    cases = [json.loads(line) for line in DATASET.read_text().splitlines() if line.strip()]
    http = httpx.Client(timeout=60)
    admin = http.post(f"{GATEWAY}/v1/auth/login", json={"user_id": "olivia.grant"})
    admin.raise_for_status()
    headers = {"authorization": f"Bearer {admin.json()['token']}"}
    users = {u["id"]: u for u in http.get(f"{GATEWAY}/v1/auth/personas").json()}
    oracle = OpaOracle(http, users)
    knobs = {
        "use_dense": not args.no_dense,
        "use_sparse": not args.no_sparse,
        "dense_k": args.dense_k,
        "sparse_k": args.sparse_k,
        "top_k": args.top_k,
    }

    def run(case: dict[str, Any]) -> dict[str, Any]:
        r = http.post(
            f"{GATEWAY}/admin/eval/retrieve",
            headers=headers,
            json={"query": case["question"], "as_user": case["persona"], **knobs},
        )
        r.raise_for_status()
        body: dict[str, Any] = r.json()
        return body

    started = time.time()
    with ThreadPoolExecutor(8) as pool:
        responses = list(pool.map(run, cases))

    per_cat: dict[str, dict[str, list[float]]] = defaultdict(lambda: defaultdict(list))
    overall: dict[str, list[float]] = defaultdict(list)
    forbidden_hits, acl_leaks, latencies, misses = [], [], [], []
    for case, resp in zip(cases, responses, strict=True):
        ranked = [c["chunk_id"] for c in resp["candidates"]]
        latencies.append(resp["retrieve_ms"])
        for c in resp["candidates"]:
            if not oracle.allowed(case["persona"], c["classification"], c["department"]):
                acl_leaks.append({"case": case["id"], "chunk": c["chunk_id"]})
        if forbidden := set(case.get("forbidden_doc_ids", [])):
            hits = sorted({c["doc_id"] for c in resp["candidates"]} & forbidden)
            if hits:
                forbidden_hits.append({"case": case["id"], "docs": hits})
        if not case["answerable"]:
            continue
        gold = set(case["gold_chunk_ids"])
        m = {
            "recall@20": recall_at(ranked, gold, 20),
            "hit@5": hit_at(ranked, gold, 5),
            "mrr": reciprocal_rank(ranked, gold),
            "ndcg@10": ndcg_at(ranked, gold, 10),
        }
        if m["recall@20"] == 0.0:
            misses.append(case["id"])
        for k, v in m.items():
            per_cat[case["category"]][k].append(v)
            overall[k].append(v)

    metrics = {k: summarize(v) for k, v in overall.items()}
    guard = guard_metrics(http, headers)
    metrics["guard_detection"] = summarize(guard["detected"])
    metrics["guard_benign_fpr"] = summarize(guard["false_positive"])
    by_category = {cat: {k: summarize(v) for k, v in ms.items()} for cat, ms in sorted(per_cat.items())}
    opa = opa_tests()
    hard = {
        "forbidden_doc_retrievals": len(forbidden_hits),
        "acl_leaks": len(acl_leaks),
        "opa_test_failures": opa["failed"],
    }

    baseline = json.loads(BASELINE.read_text()) if BASELINE.exists() else None
    regressions = []
    if baseline and not args.update_baseline:
        gated = [(n, t, True) for n, t in TOLERANCE.items()] + [
            (n, t, False) for n, t in LOWER_IS_BETTER.items()
        ]
        for name, tol, higher in gated:
            if name in baseline["metrics"] and regressed(
                baseline["metrics"][name]["mean"], metrics[name], tol, higher_is_better=higher
            ):
                regressions.append(
                    {"metric": name, "baseline": baseline["metrics"][name]["mean"], "current": metrics[name]}
                )
    passed = all(v == 0 for v in hard.values()) and not regressions

    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + "-fast"
    report = {
        "run_id": run_id,
        "suite": "fast",
        "git_sha": git_sha(),
        "label": args.label,
        "knobs": knobs,
        "dataset": {"cases": len(cases), "answerable": len(overall.get("mrr", []))},
        "metrics": metrics,
        "by_category": by_category,
        "latency_ms": {"retrieve_p50": percentile(latencies, 50), "retrieve_p95": percentile(latencies, 95)},
        "hard_gates": hard,
        "opa_tests": opa,
        "regressions": regressions,
        # Read by the Insights evals page (reference lines on the run-history chart).
        "tolerances": {
            **{k: {"tolerance": t, "higher_is_better": True} for k, t in TOLERANCE.items()},
            **{k: {"tolerance": t, "higher_is_better": False} for k, t in LOWER_IS_BETTER.items()},
        },
        "passed": passed,
        "details": {
            "forbidden": forbidden_hits[:20],
            "acl_leaks": acl_leaks[:20],
            "zero_recall_cases": misses,
        },
        "seconds": round(time.time() - started, 1),
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / f"{run_id}.json").write_text(json.dumps(report, indent=2) + "\n")
    _store(report)
    if args.update_baseline:
        BASELINE.parent.mkdir(parents=True, exist_ok=True)
        BASELINE.write_text(
            json.dumps(
                {k: report[k] for k in ("run_id", "git_sha", "knobs", "dataset", "metrics", "by_category")},
                indent=2,
            )
            + "\n"
        )

    print(f"eval-fast {run_id}  passed={passed}  ({report['seconds']}s, {len(cases)} cases)")
    for name, summary in metrics.items():
        base = (
            f"  baseline {baseline['metrics'][name]['mean']:.3f}"
            if baseline and name in baseline["metrics"]
            else ""
        )
        lo, hi = summary["ci_low"], summary["ci_high"]
        print(f"  {name:10} {summary['mean']:.3f}  [{lo:.3f}, {hi:.3f}]{base}")
    lat = report["latency_ms"]
    print(f"  hard gates {hard}   retrieve p50/p95 {lat['retrieve_p50']}/{lat['retrieve_p95']} ms")
    for r in regressions:
        print(f"  REGRESSION {r['metric']}: {r['baseline']:.3f} -> {r['current']['mean']:.3f}")
    return 0 if passed else 1


def _store(report: dict[str, Any]) -> None:
    try:
        import psycopg

        with psycopg.connect(get_settings().database_url, connect_timeout=3) as conn:
            conn.execute(
                "INSERT INTO eval_runs (run_id, git_sha, suite, metrics, passed) VALUES (%s, %s, %s, %s, %s)",
                (report["run_id"], report["git_sha"], report["suite"], json.dumps(report), report["passed"]),
            )
    except Exception as e:  # reporting must not hide the result
        print(f"  (could not store eval run in Postgres: {e})", file=sys.stderr)


if __name__ == "__main__":
    sys.exit(main())
