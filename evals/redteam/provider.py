"""promptfoo Python provider: sends the prompt to the gateway as the case persona."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "brain"))
from evals.client import Gateway  # noqa: E402

_gw = Gateway(os.environ.get("GATEWAY_URL", "http://127.0.0.1:8080"))


def call_api(prompt, options, context):  # promptfoo provider contract
    persona = (context.get("vars") or {}).get("persona", "priya.sharma")
    r = _gw.chat(persona, prompt)
    return {"output": r.text, "metadata": {"status": r.status, "sources": [s["doc_id"] for s in r.sources]}}
