"""The procurement agent (Pydantic AI) — built per request.

- Model: the gateway's internal OpenAI-compatible endpoint (:8081). The brain never holds a provider
  key: every LLM call goes through the gateway's router (fallback, breakers, quotas, cost metrics).
  The tier chosen by the Jev router travels in `x-model-tier`.
- Tools: the two MCP servers over Streamable HTTP, carrying this request's 60 s OBO token. The servers
  verify it and re-check policy; the agent has no privileges of its own.
"""

import datetime as dt
import json
from typing import Any

import httpx
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import Agent
from pydantic_ai.mcp import MCPToolset
from pydantic_ai.messages import ModelMessage, ModelRequest, ModelResponse, TextPart, UserPromptPart
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from common.settings import get_settings

PROMPT_VERSION = "agent_v1"


def instructions(name: str, role: str, dept: str) -> str:
    template = (get_settings().prompts_dir / "agent_v1.md").read_text()
    return template.format(name=name, role=role, dept=dept, today=dt.date.today().isoformat())


def toolsets(obo_token: str) -> list[MCPToolset]:
    s = get_settings()
    headers = {"Authorization": f"Bearer {obo_token}"}
    return [
        MCPToolset(Client(StreamableHttpTransport(s.mcp_procurement_url, headers=headers)), id="procurement"),
        MCPToolset(Client(StreamableHttpTransport(s.mcp_notify_url, headers=headers)), id="notify"),
    ]


def build_agent(*, tier: str, obo_token: str, name: str, role: str, dept: str) -> Agent[None, str]:
    s = get_settings()
    http = httpx.AsyncClient(headers={"x-model-tier": tier}, timeout=httpx.Timeout(90, connect=5))
    provider = OpenAIProvider(
        base_url=f"{s.gateway_internal_url}/v1",
        api_key=s.internal_api_token.get_secret_value(),
        http_client=http,
    )
    model = OpenAIChatModel("gateway", provider=provider)
    return Agent(model, instructions=instructions(name, role, dept), toolsets=toolsets(obo_token), retries=1)


def history(turns: list[tuple[str, str]]) -> list[ModelMessage]:
    out: list[ModelMessage] = []
    for role, content in turns[-8:]:
        if role == "user":
            out.append(ModelRequest(parts=[UserPromptPart(content=content)]))
        elif role == "assistant":
            out.append(ModelResponse(parts=[TextPart(content=content)]))
    return out


def as_dict(content: Any) -> dict[str, Any] | None:
    """Tool results arrive as dicts (structured MCP output) or JSON text; normalise."""
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        try:
            parsed = json.loads(content)
        except ValueError:
            return None
        return parsed if isinstance(parsed, dict) else None
    return None
