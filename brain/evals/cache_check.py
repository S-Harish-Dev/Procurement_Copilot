"""Cache acceptance checks (M7) against the live gateway: `make cache-check`.

Hard gate: **cross_scope_serves = 0**. The same question asked by a role that may *not* see the
answer must never be served from another scope's cache entry.

1. scope isolation: the document owner asks a question about their department's contract (cache
   written), then a user from another department asks the identical text. No answer-cache layer may
   report a hit, no source may come from the owner's document, and the owner's answer text must not
   come back (the other user may still get an answer from their *own* documents);
2. same-scope sharing: two Finance users share entries (proves the cache works, not just that it's off);
3. normalization: case and trailing punctuation hit the same exact entry;
4. semantic: paraphrase hit rate at the configured threshold (reported, tuned in M10), plus a
   negative control. Questions differing only in a number must never share an answer;
5. index bump: a new active index version makes every entry unreachable; switching back finds the
   old version's L2 entries again (keys are version-scoped, nothing was overwritten).

Hits are observed through Prometheus counters (`cache_requests_total{cache="answer"}`), so the
check needs no admin-only diagnostics on non-admin requests. It flushes all caches first and
temporarily switches the active index version (restored in `finally`).
"""

import json
import os
import re
import sys
import time
from collections import defaultdict
from typing import Any

import httpx
import psycopg

from common.settings import REPO_ROOT, get_settings
from evals.client import ChatResult, Gateway

GATEWAY = os.environ.get("GATEWAY_URL", "http://127.0.0.1:8080")
METRICS = os.environ.get("GATEWAY_METRICS_URL", "http://127.0.0.1:9464/metrics")
ADMIN = "olivia.grant"
INDEX_POLL_SECS = int(os.environ.get("INDEX_POLL_SECS", "10"))
LINE = re.compile(r"^cache_requests_total\{(?P<labels>[^}]*)\} (?P<value>[0-9.e+]+)$")


def answer_hits() -> dict[str, float]:
    """Cumulative answer-cache hits per layer (l1, l2, semantic)."""
    out: dict[str, float] = defaultdict(float)
    for line in httpx.get(METRICS, timeout=10).text.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        labels = dict(kv.split("=", 1) for kv in m["labels"].split(","))
        labels = {k: v.strip('"') for k, v in labels.items()}
        if labels.get("cache") == "answer" and labels.get("result") == "hit":
            out[labels["layer"]] += float(m["value"])
    return out


def delta(before: dict[str, float], after: dict[str, float]) -> dict[str, float]:
    return {
        k: after.get(k, 0.0) - before.get(k, 0.0)
        for k in set(before) | set(after)
        if after.get(k, 0.0) != before.get(k, 0.0)
    }


def ask(g: Gateway, persona: str, text: str) -> tuple[ChatResult, dict[str, float]]:
    """Chat once (waiting out rate limiting) and return which answer-cache layers hit."""
    for _ in range(30):
        before = answer_hits()
        r = g.chat(persona, text)
        if r.http_status != 429:
            return r, delta(before, answer_hits())
        time.sleep(2)
    return r, {}


def load_cases() -> list[dict[str, Any]]:
    path = REPO_ROOT / "evals" / "datasets" / "qa.jsonl"
    return [json.loads(x) for x in path.read_text().splitlines() if x.strip()]


def main() -> int:
    started = time.time()
    run_id = time.strftime("%Y%m%dT%H%M%SZ-cache", time.gmtime())
    g = Gateway(GATEWAY)
    admin = {"authorization": f"Bearer {g.token(ADMIN)}"}
    g.http.post(f"{GATEWAY}/admin/cache/flush", headers=admin).raise_for_status()
    cases = load_cases()
    report: dict[str, Any] = {"run_id": run_id}
    hard: dict[str, list[Any]] = {"cross_scope_serves": []}

    # 1. scope isolation ------------------------------------------------------------------------
    owners: dict[str, str] = {}
    for c in cases:
        m = re.search(r"\bC-\d{4}\b", c["question"])
        if c["category"] == "contract_fact" and m and c["answerable"]:
            owners.setdefault(m.group(0), c["persona"])
    isolation = []
    for c in cases:
        if c["category"] != "role_restricted" or not c["id"].startswith("crossdept-"):
            continue
        contract = re.search(r"\bC-\d{4}\b", c["question"])
        owner = owners.get(contract.group(0)) if contract else None
        if not owner or owner == c["persona"]:
            continue
        first, _ = ask(g, owner, c["question"])
        if first.status != "answered":
            continue  # nothing was cached for this question; not a usable probe
        _, hit_owner = ask(g, owner, c["question"])
        other, hit_other = ask(g, c["persona"], c["question"])
        # A cross-scope serve is any answer-cache hit for the other user, any source from the
        # owner's restricted document, or the owner's exact answer text when that answer drew on
        # the restricted document (both may legitimately cite the same public one). Answering from
        # the other user's *own* visible documents is not a leak: the offline mock LLM never abstains.
        forbidden = set(c.get("forbidden_doc_ids", []))
        leaked = (
            bool(hit_other)
            or any(src.get("doc_id") in forbidden for src in other.sources)
            or (
                other.status == "answered"
                and other.text == first.text
                and any(src.get("doc_id") in forbidden for src in first.sources)
            )
        )
        isolation.append(
            {
                "question": c["question"],
                "owner": owner,
                "other": c["persona"],
                "owner_repeat_hit": hit_owner,
                "other_status": other.status,
                "other_doc_ids": sorted({src.get("doc_id", "") for src in other.sources}),
                "other_hits": hit_other,
                "leaked": leaked,
            }
        )
        if leaked:
            hard["cross_scope_serves"].append(isolation[-1])
        if len(isolation) >= 10:
            break
    report["isolation"] = {
        "probes": len(isolation),
        "owner_repeat_hit_rate": sum(1 for i in isolation if i["owner_repeat_hit"]) / max(len(isolation), 1),
        "cases": isolation,
    }

    # 2. same-scope sharing + 3. normalization ---------------------------------------------------
    q = "What is the hotel cap per night in tier-1 cities?"
    a1, _ = ask(g, "elena.petrova", q)
    a2, shared = ask(g, "ravi.iyer", q)
    a3, normalized = ask(g, "ravi.iyer", "  what is the HOTEL cap per night in tier-1 cities ")
    report["sharing"] = {
        "first_status": a1.status,
        "same_scope_hit": shared,
        "normalized_hit": normalized,
        "same_text": a1.text == a2.text == a3.text,
    }

    # 4. semantic ---------------------------------------------------------------------------------
    by_id = {c["id"]: c for c in cases}
    para = []
    for c in cases:
        if c["category"] != "paraphrase" or c.get("paraphrase_of") not in by_id:
            continue
        orig = by_id[c["paraphrase_of"]]
        r0, _ = ask(g, orig["persona"], orig["question"])
        _, hit = ask(g, orig["persona"], c["question"])
        para.append(
            {"original": orig["question"], "paraphrase": c["question"], "orig_status": r0.status, "hit": hit}
        )
    negatives = []
    for base, changed in [
        ("Above 25k, how many quotes are required?", "Above 50k, how many quotes are required?"),
        (
            "What is the hotel cap per night in tier-1 cities?",
            "What is the hotel cap per night in tier-2 cities?",
        ),
    ]:
        ask(g, "priya.sharma", base)
        r, hit = ask(g, "priya.sharma", changed)
        negatives.append({"base": base, "changed": changed, "hit": hit})
        if hit:
            hard.setdefault("numeric_semantic_hits", []).append(negatives[-1])
    answered = [p for p in para if p["orig_status"] == "answered"]
    report["semantic"] = {
        "paraphrase_pairs": len(para),
        "answered_originals": len(answered),
        "semantic_hit_rate": sum(1 for p in answered if p["hit"].get("semantic")) / max(len(answered), 1),
        "pairs": para,
        "numeric_negative_controls": negatives,
    }

    # 5. index bump --------------------------------------------------------------------------------
    with psycopg.connect(get_settings().database_url, autocommit=True) as db:
        active = db.execute("SELECT version FROM index_versions WHERE is_active").fetchone()
        assert active, "no active index version"
        v = int(active[0])
        fake = v + 100_000
        bump: dict[str, Any] = {"from": v, "to": fake}
        try:
            db.execute(
                "INSERT INTO index_versions (version, notes) VALUES (%s, 'cache_check: temporary')"
                " ON CONFLICT DO NOTHING",
                (fake,),
            )
            db.execute("UPDATE index_versions SET is_active = false WHERE version = %s", (v,))
            db.execute("UPDATE index_versions SET is_active = true WHERE version = %s", (fake,))
            time.sleep(INDEX_POLL_SECS + 3)
            r, hit = ask(g, "elena.petrova", q)
            bump["after_bump"] = {"status": r.status, "hits": hit}
            if hit:
                hard.setdefault("served_after_index_bump", []).append(bump["after_bump"])
        finally:
            db.execute("UPDATE index_versions SET is_active = false WHERE version = %s", (fake,))
            db.execute("UPDATE index_versions SET is_active = true WHERE version = %s", (v,))
            db.execute("DELETE FROM index_versions WHERE version = %s", (fake,))
        time.sleep(INDEX_POLL_SECS + 3)
        r, hit = ask(g, "elena.petrova", q)
        bump["after_restore"] = {"status": r.status, "hits": hit}
    report["index_bump"] = bump

    counts = {k: len(v) for k, v in hard.items()}
    passed = all(n == 0 for n in counts.values()) and len(isolation) >= 5
    report.update(
        {"passed": passed, "hard_gates": counts, "seconds": round(time.time() - started, 1), "details": hard}
    )
    out = REPO_ROOT / "evals" / "reports" / f"{run_id}.json"
    out.write_text(json.dumps(report, indent=2) + "\n")

    print(f"cache-check {run_id} passed={passed} ({report['seconds']}s) hard gates: {counts}")
    iso = report["isolation"]
    print(
        f"  isolation: {iso['probes']} probes, owner repeat hit rate {iso['owner_repeat_hit_rate']:.0%},"
        f" leaks {counts['cross_scope_serves']}"
    )
    print(f"  sharing: {report['sharing']}")
    sem = report["semantic"]
    print(
        f"  semantic: {sem['semantic_hit_rate']:.0%} of {sem['answered_originals']}"
        " answered paraphrase pairs hit;"
        f" numeric negatives hit: {sum(1 for n in negatives if n['hit'])}/{len(negatives)}"
    )
    print(f"  index bump: {bump}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
