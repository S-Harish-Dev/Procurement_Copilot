# ADR-12: Agent on Pydantic AI, tools over MCP (Streamable HTTP), LLM access through the gateway

- Status: accepted (2026-10-30)

## Decision
- The brain (Python 3.14) runs one Pydantic AI 2.63 agent per request behind gRPC (`proto/brain.proto`:
  `RunAgent`, `ExecuteConfirmed`, `CallTool`). Its model is the gateway's internal proxy (:8081), so the brain
  holds no provider key and every LLM call gets the gateway's fallback, quotas, metrics and output guard.
- Tools come from two MCP servers (official MCP SDK 2.3, stateless Streamable HTTP, DNS-rebinding protection):
  `acme-procurement` (11 tools) and `acme-notify` (template-only e-mail, chat feed, PO PDF).
- Every MCP call carries a 60-second on-behalf-of (OBO) JWT (aud `mcp`); each server verifies it and re-asks OPA.
  Write tools only *propose* until a human confirms (ADR-10).
- List-returning tools return `{"items": [...], "count": n}`: unannotated list returns arrived as one text block
  per element and the client read only the first (bug found in M5).

## Why
- Pydantic AI is typed, model-agnostic and light; MCP makes the tools reusable by any MCP client and keeps
  authorization at the tool boundary instead of in prompt text.
- Routing the agent's LLM traffic through the gateway keeps one place for cost, fallback, guardrails and traces.

## Measured
`make agent-tasks` (37 tasks): 37/37, unauthorized_actions = 0 (M5; restored in M8 after the harness change).
Full persona flow verified end-to-end in M5: create → manager → finance → approved, budget committed, PO PDF,
Mailpit e-mails; self-approval and over-limit refused with clear messages.

## Limits
Offline the mock LLM plans only scripted tool calls, so natural-language planning quality is untested until keys
exist (`agent_tasks run --natural`). Since M8 scripted directives travel in a `script` field accepted only with
`EVAL_ENDPOINTS=true`; typed into the message they are (correctly) scored as injections.

## Alternatives rejected
- *LangChain/LangGraph*: heavier abstraction for a single-agent, few-tool flow.
- *Tools as in-process Python functions*: no protocol boundary for re-authorization and no reuse by other clients.
- *Brain calling providers directly*: duplicate keys, fallback and cost accounting; the guard would be bypassed.
