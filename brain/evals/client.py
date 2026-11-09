"""Minimal gateway client used by evals, red-team checks and smoke scripts.

Logs in as a demo persona and runs `POST /v1/chat`, parsing the SSE stream into a result object
(answer text, status, sources, admin diagnostics, timing).
"""

import json
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class ChatResult:
    status: str = ""
    proposals: list[dict[str, Any]] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    result: Any = None
    text: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    diagnostics: dict[str, Any] | None = None
    events: list[str] = field(default_factory=list)
    seconds: float = 0.0
    first_delta_seconds: float | None = None
    http_status: int = 200


class Gateway:
    def __init__(self, base_url: str = "http://127.0.0.1:8080", timeout: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.http = httpx.Client(timeout=timeout)
        self._tokens: dict[str, str] = {}

    def token(self, user_id: str) -> str:
        if user_id not in self._tokens:
            r = self.http.post(f"{self.base_url}/v1/auth/login", json={"user_id": user_id})
            r.raise_for_status()
            self._tokens[user_id] = r.json()["token"]
        return self._tokens[user_id]

    def chat(
        self,
        user_id: str,
        message: str,
        history: list[dict[str, str]] | None = None,
        script: str | None = None,
    ) -> ChatResult:
        """`script`: offline mock-LLM tool directive, sent outside the guarded message (gateway
        accepts it only with EVAL_ENDPOINTS=true)."""
        body: dict[str, Any] = {"message": message, "history": history or []}
        if script is not None:
            body["script"] = script
        return self._sse(user_id, "/v1/chat", body)

    def confirm(self, user_id: str, proposal_id: str) -> ChatResult:
        return self._sse(user_id, f"/v1/actions/{proposal_id}/confirm", None)

    def tool(self, user_id: str, name: str, args: dict[str, Any] | None = None) -> httpx.Response:
        headers = {"authorization": f"Bearer {self.token(user_id)}"}
        return self.http.post(f"{self.base_url}/v1/tools/{name}", json={"args": args or {}}, headers=headers)

    def get(self, user_id: str, path: str) -> httpx.Response:
        headers = {"authorization": f"Bearer {self.token(user_id)}"}
        return self.http.get(f"{self.base_url}{path}", headers=headers)

    def _sse(self, user_id: str, path: str, body: dict[str, Any] | None) -> ChatResult:
        res = ChatResult()
        started = time.perf_counter()
        headers = {"authorization": f"Bearer {self.token(user_id)}"}
        with self.http.stream("POST", f"{self.base_url}{path}", json=body, headers=headers) as r:
            res.http_status = r.status_code
            if r.status_code != 200:
                r.read()
                res.status = f"http_{r.status_code}"
                res.text = r.text
                res.seconds = time.perf_counter() - started
                return res
            event = ""
            for line in r.iter_lines():
                if line.startswith("event:"):
                    event = line.split(":", 1)[1].strip()
                    res.events.append(event)
                elif line.startswith("data:"):
                    data = json.loads(line.split(":", 1)[1].strip())
                    if event == "delta":
                        if res.first_delta_seconds is None:
                            res.first_delta_seconds = time.perf_counter() - started
                        res.text += data["text"]
                    elif event == "replace":
                        res.text = data["text"]
                    elif event == "sources":
                        res.sources = data
                    elif event == "proposal":
                        res.proposals.append(data)
                    elif event == "tool":
                        res.tools.append(data)
                    elif event == "done":
                        res.status = data["status"]
                        res.diagnostics = data.get("diagnostics")
                        res.result = data.get("result")
        res.seconds = time.perf_counter() - started
        return res
