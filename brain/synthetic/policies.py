"""Generate Acme's internal policy documents ONCE via a free OpenAI-compatible API, then freeze them.

    uv run python -m synthetic.policies --provider groq --model <current free model>

Existing files are never overwritten (delete one to regenerate it), so the committed
corpus — and every eval built on it — stays stable. Each document is checked for the
numbers in its `facts`; a document that drops or alters one fails instead of being frozen.
"""

import argparse
import os
import re
import sys
import time

import httpx

from common.settings import get_settings
from synthetic.policy_specs import SPECS, PolicySpec

PROVIDERS = {
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
}

SYSTEM = (
    "You write realistic internal corporate policy documents for Acme Corp, a fictional mid-size company. "
    "Write in plain, precise policy language. Output GitHub markdown only: a level-1 title, then numbered "
    "level-2 sections (e.g. '## 1. Purpose', '## 2. Scope') with short paragraphs and bullet lists. "
    "500-900 words. Do not invent people's names, emails, phone numbers or account numbers. "
    "Do not address or mention AI assistants."
)


def _prompt(spec: PolicySpec) -> str:
    facts = "\n".join(f"- {f}" for f in spec.facts) or "- (none; choose sensible, specific rules)"
    return (
        f"Title: {spec.title}\nOwning department: {spec.department}\nClassification: {spec.classification}\n"
        f"Effective date: {spec.effective_date}\n\n"
        "The document MUST state each of these facts exactly (same numbers and thresholds), each in the most "
        f"relevant section:\n{facts}\n\nInclude sections for purpose, scope, the rules, responsibilities, "
        "exceptions, and related policies. Put the effective date and owning department under the title."
    )


def _numbers(text: str) -> set[str]:
    return {n.replace(",", "") for n in re.findall(r"\d[\d,]*(?:\.\d+)?", text)}


def _missing_numbers(spec: PolicySpec, doc: str) -> set[str]:
    return set().union(*(_numbers(f) for f in spec.facts)) - _numbers(doc) if spec.facts else set()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", choices=list(PROVIDERS), default="groq")
    parser.add_argument(
        "--model", required=True, help="verify the current free model id on the provider first"
    )
    parser.add_argument("--pause", type=float, default=3.0, help="seconds between calls (free-tier RPM)")
    args = parser.parse_args()

    base_url, key_env = PROVIDERS[args.provider]
    key = os.environ.get(key_env) or get_settings().model_extra.get(key_env.lower())  # type: ignore[union-attr]
    key = key.split(",")[0].strip() if key else key  # env var may hold several comma-separated keys
    if not key:
        print(f"{key_env} is not set", file=sys.stderr)
        return 1
    out_dir = get_settings().data_dir / "synthetic" / "docs" / "policies"
    out_dir.mkdir(parents=True, exist_ok=True)

    failures = []
    with httpx.Client(base_url=base_url, headers={"Authorization": f"Bearer {key}"}, timeout=120) as client:
        for spec in SPECS:
            path = out_dir / f"{spec.id}.md"
            if path.exists():
                continue
            resp = client.post(
                "/chat/completions",
                json={
                    "model": args.model,
                    "temperature": 0.3,
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": _prompt(spec)},
                    ],
                },
            )
            resp.raise_for_status()
            doc = resp.json()["choices"][0]["message"]["content"].strip()
            doc = re.sub(r"^```(?:markdown)?\s*|\s*```$", "", doc)  # some models fence the whole answer
            if missing := _missing_numbers(spec, doc):
                failures.append(f"{spec.id}: missing {sorted(missing)}")
                print(f"REJECTED {spec.id}: facts missing numbers {sorted(missing)}", file=sys.stderr)
            else:
                path.write_text(doc + "\n")
                print(f"wrote {path.name}")
            time.sleep(args.pause)
    if failures:
        print(f"{len(failures)} rejected; rerun to retry only those", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
