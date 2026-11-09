"""Turn the latest `scripts/loadtest.sh` run (k6 summary exports) into `docs/loadtest.md`.

The raw summaries stay next to it in `evals/loadtest/<run>/` so every number can be re-checked.
"""

import json
import sys
from pathlib import Path
from typing import Any

from common.settings import REPO_ROOT

RUNS = REPO_ROOT / "evals" / "loadtest"
SCENARIOS = {
    "cache_hit": ("Exact answer-cache hit (full gateway path, no retrieval/LLM)", "1–5k RPS"),
    "retrieval": ("Retrieval only: embed + BM25 + hybrid Qdrant with ACL filter", "p95 < 100 ms at 100 VUs"),
    "e2e_mock": ("Full Q&A, caches off, mock Jev + mock LLM", "reported (no target)"),
}


def metric(summary: dict[str, Any], name: str) -> dict[str, Any]:
    m: dict[str, Any] = summary.get("metrics", {}).get(name, {})
    values: dict[str, Any] = m.get("values", m)  # newer k6 summaries nest values under "values"
    return values


def row(name: str, s: dict[str, Any], vus: str) -> str:
    dur = metric(s, "http_req_duration")
    reqs = metric(s, "http_reqs")
    checks = metric(s, "checks")
    ok = checks.get("value", checks.get("rate"))
    what, target = SCENARIOS[name]
    return (
        f"| `{name}` | {what} | {vus} | {reqs.get('rate', 0):,.0f} | {dur.get('med', 0):,.1f} | "
        f"{dur.get('p(95)', 0):,.1f} | {dur.get('p(99)', 0):,.1f} | {ok:.1%} | {target} |"
    )


def main() -> int:
    runs = sorted(p for p in RUNS.iterdir() if p.is_dir()) if RUNS.exists() else []
    if not runs:
        sys.exit("no load-test runs in evals/loadtest (run scripts/loadtest.sh)")
    run: Path = runs[-1]
    meta = json.loads((run / "meta.json").read_text())
    lines = [
        "# Load test report",
        "",
        f"Run `{meta['run']}` on `{meta['host']}` ({meta['cores']} cores, shared machine, load average "
        f"before: {meta['loadavg_before']}), git `{meta['git_sha']}`, {meta['duration']} per scenario. "
        "Offline: mock LLM and mock Jev. Per-user rate limits lifted for the run. Raw k6 summaries: "
        f"`evals/loadtest/{run.name}/`.",
        "",
        "| Scenario | Path | VUs | RPS | p50 ms | p95 ms | p99 ms | Checks OK | Target |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for name in SCENARIOS:
        f = run / f"{name}.json"
        if f.exists():
            s = json.loads(f.read_text())
            vus = metric(s, "vus_max").get("max", metric(s, "vus_max").get("value", "?"))
            lines.append(row(name, s, str(vus)))
        else:
            lines.append(f"| `{name}` | {SCENARIOS[name][0]} | – | not run | | | | | {SCENARIOS[name][1]} |")
    lines += ["", "Interpretation and the bottleneck analysis are in Limitation.md §12."]
    (REPO_ROOT / "docs" / "loadtest.md").write_text("\n".join(lines) + "\n")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
