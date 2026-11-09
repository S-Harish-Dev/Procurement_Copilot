"""Chaos drills for every row of the resilience matrix: `make chaos`.

Each drill stops one or more dependencies, sends real requests through the public API, checks the
behaviour the matrix promises, restarts the dependencies and checks recovery. Results go to
`evals/reports/<run>-chaos.json` and `docs/chaos-drills.md`.

    uv run python -m evals.chaos                 # native stack (scripts/native-stack.sh)
    uv run python -m evals.chaos --mode compose  # docker compose (infra/docker-compose.yml)

Caches are flushed before every drill so "fresh" questions really exercise the dependency.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from common.settings import REPO_ROOT
from evals.client import ChatResult, Gateway

GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8080")
METRICS = os.environ.get("GATEWAY_METRICS_URL", "http://127.0.0.1:9464/metrics")
ADMIN = "olivia.grant"
UNAVAILABLE = "The assistant is temporarily unavailable. Please try again shortly."
NO_ANSWER = "I couldn't find information on this in the available procurement documents."
STATE = REPO_ROOT / ".native"


class Control:
    """Stops and starts dependencies on the native stack or under docker compose."""

    def __init__(self, mode: str) -> None:
        self.mode = mode

    def stop(self, svc: str) -> None:
        if self.mode == "compose":
            self._compose("stop", svc)
        elif svc == "postgres":
            bin_dir = os.path.expanduser("~/.local/opt/pc-stack/bin")
            subprocess.run(
                [f"{bin_dir}/pg_ctl", "-D", str(STATE / "pg"), "stop", "-m", "fast"],
                check=True,
                capture_output=True,
            )
        else:
            pid_file = STATE / f"{svc}.pid"
            os.kill(int(pid_file.read_text()), signal.SIGTERM)
            pid_file.unlink()
        time.sleep(1.5)

    def start_all(self, services: list[str]) -> None:
        if self.mode == "compose":
            for svc in services:
                self._compose("start", svc)
        else:
            # `start` respawns only what isn't running.
            subprocess.run(
                [str(REPO_ROOT / "scripts/native-stack.sh"), "start"], check=True, capture_output=True
            )

    def _compose(self, verb: str, svc: str) -> None:
        subprocess.run(
            [
                "docker",
                "compose",
                "-f",
                str(REPO_ROOT / "infra/docker-compose.yml"),
                "--env-file",
                str(REPO_ROOT / ".env"),
                verb,
                svc,
            ],
            check=True,
            capture_output=True,
        )


@dataclass
class Drill:
    name: str
    failure: str
    expected: str
    services: list[str]
    probe: Callable[[Ctx], list[tuple[str, bool, Any]]]
    warm: Callable[[Ctx], None] | None = None


@dataclass
class Ctx:
    g: Gateway
    admin_headers: dict[str, str]
    notes: dict[str, Any] = field(default_factory=dict)
    seq: int = 0

    def fresh(self) -> str:
        """The next real question from a rotating pool. Caches are flushed before every drill, so a
        question is uncached the first time a drill asks it. (Appending a unique suffix instead broke
        the offline mock gate, which scores relevance by word overlap: every probe became no_match.)"""
        self.seq += 1
        return POOL[self.seq % len(POOL)]

    def chat(self, persona: str, text: str, script: str | None = None) -> ChatResult:
        for _ in range(20):
            r = self.g.chat(persona, text, script=script)
            if r.http_status != 429:
                return r
            time.sleep(2)
        return r

    def tool(self, persona: str, name: str, args: dict[str, Any] | None = None) -> tuple[int, Any]:
        r = self.g.tool(persona, name, args)
        try:
            return r.status_code, r.json()
        except ValueError:
            return r.status_code, r.text


def metric(name: str, labels: dict[str, str] | None = None) -> float:
    total = 0.0
    for line in httpx.get(METRICS, timeout=5).text.splitlines():
        if not line.startswith(name):
            continue
        head, _, value = line.rpartition(" ")
        if labels and not all(f'{k}="{v}"' in head for k, v in labels.items()):
            continue
        if head == name or head.startswith(name + "{"):
            total += float(value)
    return total


Q_POLICY = "What is the hotel cap per night in tier-1 cities?"
POOL = [
    "How long does vendor onboarding take?",
    "How many quotes are needed above USD 25,000?",
    "What is the replacement cycle for a standard laptop?",
    "What documents are required to add a new vendor?",
    "Who approves purchase requests above USD 25,000?",
    "What is the per diem for meals on international travel?",
    "Can I use a corporate card for software subscriptions?",
    "When must suspected procurement fraud be reported?",
]
Q_PUBLIC = "What does the inspection and acceptance clause require of the contractor?"


def degraded(r: ChatResult) -> list[str]:
    return list((r.diagnostics or {}).get("degraded", []))


def jev_down(c: Ctx) -> list[tuple[str, bool, Any]]:
    r = c.chat(ADMIN, Q_POLICY)
    # The breaker opens after 3 consecutive failures; one chat makes 2 Jev calls (route + gate).
    c.chat(ADMIN, c.fresh())
    time.sleep(12)  # the gateway exports the jev_degraded gauge on its poll loop
    gauge = metric("jev_degraded")
    return [
        ("question still answered", r.status == "answered", r.status),
        ("diagnostics mark jev degraded (admin only)", "jev" in degraded(r), degraded(r)),
        ("jev_degraded gauge = 1", gauge == 1.0, gauge),
    ]


def llm_primary_down(c: Ctx) -> list[tuple[str, bool, Any]]:
    before = metric("llm_fallback_total")
    r = c.chat(ADMIN, c.fresh())
    provider = (r.diagnostics or {}).get("provider")
    after = metric("llm_fallback_total")
    return [
        ("question still answered", r.status == "answered", r.status),
        ("served by the fallback provider", bool(provider) and "secondary" in str(provider), provider),
        ("llm_fallback_total increased", after > before, [before, after]),
    ]


def warm_cached(c: Ctx) -> None:
    r = c.chat("priya.sharma", Q_POLICY)
    c.notes["warm_status"] = r.status


def llm_all_down(c: Ctx) -> list[tuple[str, bool, Any]]:
    fresh = c.chat("priya.sharma", c.fresh())
    cached = c.chat("priya.sharma", Q_POLICY)
    return [
        (
            "fresh question gets the professional 'temporarily unavailable' message",
            fresh.status == "unavailable" and fresh.text == UNAVAILABLE,
            [fresh.status, fresh.text[:80]],
        ),
        ("cached answer still served", cached.status == "answered", cached.status),
    ]


def valkey_down(c: Ctx) -> list[tuple[str, bool, Any]]:
    r = c.chat("marcus.lee", c.fresh())
    statuses = [c.g.chat("noah.williams", c.fresh()).http_status for _ in range(3)]
    return [
        ("question answered without Valkey (L1 only)", r.status == "answered", r.status),
        (
            "rate limiting falls back to in-process buckets (requests still admitted)",
            all(s == 200 for s in statuses),
            statuses,
        ),
    ]


def qdrant_down(c: Ctx) -> list[tuple[str, bool, Any]]:
    r = c.chat("priya.sharma", c.fresh())
    code, body = c.tool("priya.sharma", "list_my_requests")
    return [
        (
            "Q&A returns 'temporarily unavailable'",
            r.status == "unavailable" and r.text == UNAVAILABLE,
            [r.status, r.text[:80]],
        ),
        (
            "action path still works (list_my_requests)",
            code == 200 and isinstance(body, dict) and "items" in body,
            [code, str(body)[:120]],
        ),
    ]


def opa_down(c: Ctx) -> list[tuple[str, bool, Any]]:
    r = c.chat("priya.sharma", Q_PUBLIC)
    ret = c.g.http.post(
        f"{GATEWAY}/admin/eval/retrieve",
        headers=c.admin_headers,
        json={"query": "FY2026 IT budget allocation memo", "as_user": "elena.petrova"},
    ).json()
    classes = sorted({x["classification"] for x in ret.get("candidates", [])})
    code, body = c.tool("james.chen", "approve_request", {"pr_id": "PR-2026-00309", "comment": "drill"})
    status = body.get("status") if isinstance(body, dict) else None
    return [
        (
            "public Q&A continues",
            r.status in ("answered", "no_answer") and r.text != UNAVAILABLE,
            [r.status, r.text[:80]],
        ),
        (
            "retrieval scope fails closed to public only (even for Finance)",
            classes in ([], ["public"]),
            classes,
        ),
        (
            "writes denied",
            status in ("denied", "unavailable", "error") and status != "proposal",
            [code, status],
        ),
    ]


def postgres_down(c: Ctx) -> list[tuple[str, bool, Any]]:
    code, body = c.tool("priya.sharma", "list_my_requests")
    msg = body.get("message") if isinstance(body, dict) else str(body)
    r = c.chat("priya.sharma", c.fresh())
    return [
        (
            "actions unavailable with a clear message (no stack trace)",
            isinstance(body, dict)
            and body.get("status") != "ok"
            and "items" not in body
            and "Traceback" not in str(body),
            [code, str(msg)[:120]],
        ),
        ("Q&A still works", r.status == "answered", r.status),
    ]


def brain_down(c: Ctx) -> list[tuple[str, bool, Any]]:
    act = c.chat("priya.sharma", "show my requests", script="[[call list_my_requests {}]]")
    qa = c.chat("priya.sharma", c.fresh())
    return [
        (
            "action intent gets 'temporarily unavailable'",
            act.text == UNAVAILABLE,
            [act.status, act.text[:80]],
        ),
        ("Q&A unaffected", qa.status == "answered", qa.status),
    ]


DRILLS = [
    Drill(
        "jev_down",
        "Jev unavailable (connection refused)",
        "Breaker opens → degraded mode; jev_degraded=1; admin badge",
        ["mock-jev"],
        jev_down,
    ),
    Drill(
        "llm_primary_down",
        "Primary LLM provider down",
        "Retry with backoff → next provider",
        ["mock-llm"],
        llm_primary_down,
    ),
    Drill(
        "llm_all_down",
        "All LLM providers down",
        "'Temporarily unavailable' message; cached answers still served",
        ["mock-llm", "mock-llm-2"],
        llm_all_down,
        warm=warm_cached,
    ),
    Drill("valkey_down", "Valkey down", "L1 only; in-process rate limiting", ["valkey"], valkey_down),
    Drill(
        "qdrant_down",
        "Qdrant down",
        "Q&A 'temporarily unavailable'; action path works",
        ["qdrant"],
        qdrant_down,
    ),
    Drill(
        "opa_down",
        "OPA down",
        "Fail closed: deny writes and non-public docs; public Q&A continues",
        ["opa"],
        opa_down,
    ),
    Drill(
        "postgres_down",
        "Postgres down",
        "Actions unavailable (clear message); Q&A works",
        ["postgres"],
        postgres_down,
    ),
    Drill(
        "brain_down",
        "Brain down",
        "Action intents 'temporarily unavailable'; Q&A unaffected",
        ["brain"],
        brain_down,
    ),
]


def recovered(c: Ctx) -> tuple[bool, float]:
    """A computed (uncached), fully healthy answer end to end; neither a cached nor a degraded
    answer can fake recovery."""
    started = time.time()
    while time.time() - started < 90:
        try:
            c.g.http.post(f"{GATEWAY}/admin/cache/flush", headers=c.admin_headers)
            # Admin probe: "recovered" means answered with *nothing* degraded (breakers closed again),
            # not merely answered in degraded mode, which would leak into the next drill.
            r = c.chat(ADMIN, c.fresh())
            if r.status == "answered" and not degraded(r):
                return True, round(time.time() - started, 1)
        except httpx.HTTPError:
            pass
        time.sleep(3)
    return False, round(time.time() - started, 1)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["native", "compose"], default="native")
    ap.add_argument("--only", nargs="*")
    args = ap.parse_args()
    ctl = Control(args.mode)
    g = Gateway(GATEWAY, timeout=60)
    for persona in [
        "priya.sharma",
        "marcus.lee",
        "noah.williams",
        "sofia.alvarez",
        "james.chen",
        "elena.petrova",
    ]:
        g.token(persona)  # log in while Postgres is up
    c = Ctx(g, {"authorization": f"Bearer {g.token(ADMIN)}"})
    run_id = time.strftime("%Y%m%dT%H%M%SZ-chaos", time.gmtime())
    results: list[dict[str, Any]] = []
    for d in DRILLS:
        if args.only and d.name not in args.only:
            continue
        g.http.post(f"{GATEWAY}/admin/cache/flush", headers=c.admin_headers)
        for p in ["priya.sharma", "marcus.lee", "noah.williams", "sofia.alvarez", ADMIN]:
            g.http.post(f"{GATEWAY}/admin/users/{p}/reset-strikes", headers=c.admin_headers)
        if d.warm:
            d.warm(c)
        print(f"== {d.name}: stopping {d.services}")
        for svc in d.services:
            ctl.stop(svc)
        try:
            checks = d.probe(c)
        except Exception as e:  # a drill that crashes is a failed drill, never a skipped one
            checks = [("probe ran", False, repr(e))]
        finally:
            ctl.start_all(d.services)
        ok, secs = recovered(c)
        checks.append((f"recovered after restart ({secs}s)", ok, secs))
        passed = all(ok_ for _, ok_, _ in checks)
        for name, ok_, got in checks:
            print(f"   {'PASS' if ok_ else 'FAIL'}  {name}: {json.dumps(got, default=str)[:160]}")
        results.append(
            {
                "drill": d.name,
                "failure": d.failure,
                "expected": d.expected,
                "stopped": d.services,
                "passed": passed,
                "checks": [{"check": n, "passed": ok_, "observed": o} for n, ok_, o in checks],
            }
        )
    report = {
        "run_id": run_id,
        "mode": args.mode,
        "passed": all(r["passed"] for r in results),
        "drills": results,
    }
    (REPO_ROOT / "evals" / "reports" / f"{run_id}.json").write_text(
        json.dumps(report, indent=2, default=str) + "\n"
    )
    write_markdown(report)
    n_passed = sum(1 for r in results if r["passed"])
    print(f"chaos {run_id} passed={report['passed']} ({n_passed}/{len(results)} drills)")
    return 0 if report["passed"] else 1


def write_markdown(report: dict[str, Any]) -> None:
    lines = [
        "# Chaos drills (resilience matrix)",
        "",
        f"Run `{report['run_id']}` ({report['mode']} stack, offline: mock LLMs and mock Jev). Generated by "
        "`make chaos` (`brain/evals/chaos.py`); every drill stops real processes and probes the public API.",
        "",
        "| Failure | Expected | Observed | Result |",
        "|---|---|---|---|",
    ]
    for r in report["drills"]:
        observed = "; ".join(f"{'✓' if ch['passed'] else '✗'} {ch['check']}" for ch in r["checks"])
        lines.append(
            f"| {r['failure']} | {r['expected']} | {observed} | {'pass' if r['passed'] else '**FAIL**'} |"
        )
    lines += ["", "Raw observations (statuses, provider names, metric values) are in the JSON report."]
    (REPO_ROOT / "docs" / "chaos-drills.md").write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    sys.exit(main())
