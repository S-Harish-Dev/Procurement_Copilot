"""Builds promptfoo tests from cases.jsonl (one source of truth for both harnesses)."""
import json
from pathlib import Path


def generate_tests(config=None):
    tests = []
    for line in (Path(__file__).parent / "cases.jsonl").read_text().splitlines():
        if not line.strip():
            continue
        c = json.loads(line)
        asserts = [{"type": "not-icontains", "value": m} for m in c.get("forbidden_markers", [])]
        tests.append({"description": f"{c['category']}: {c['id']}", "vars": {"prompt": c["prompt"], "persona": c["persona"]},
                      "assert": asserts})
    return tests
