"""gRPC server for the gateway (proto/brain.proto).

RunAgent: runs the Pydantic AI agent and streams tool calls, text and proposals as they happen.
ExecuteConfirmed: executes a confirmed proposal WITHOUT an LLM — loads the stored payload and calls
the proposed MCP tool with the one-time confirmation token the gateway minted after the human click.

    uv run python -m agent.server
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from typing import Any

import grpc
import psycopg
from fastmcp.client import Client
from fastmcp.client.transports import StreamableHttpTransport
from pydantic_ai import AgentRunResultEvent
from pydantic_ai.messages import FunctionToolCallEvent, FunctionToolResultEvent, PartDeltaEvent, TextPartDelta

from agent.agent import as_dict, build_agent, history
from agent.pb import brain_pb2 as pb
from agent.pb import brain_pb2_grpc as pb_grpc
from common.settings import get_settings

log = logging.getLogger("brain")
AGENT_TIMEOUT_S = 120


def _event(**kw: Any) -> pb.AgentEvent:
    return pb.AgentEvent(**kw)


class Brain(pb_grpc.BrainServicer):  # type: ignore[misc]  # generated base class is untyped
    async def RunAgent(
        self, request: pb.AgentRequest, context: grpc.aio.ServicerContext
    ) -> AsyncIterator[pb.AgentEvent]:
        c = request.claims
        agent = build_agent(
            tier=request.model_tier or "cheap",
            obo_token=request.obo_token,
            name=c.name,
            role=c.role,
            dept=c.dept,
        )
        turns = [(t.role, t.content) for t in request.history]
        status, final_text, proposals = "done", "", 0
        try:
            async with asyncio.timeout(AGENT_TIMEOUT_S):
                stream = agent.run_stream_events(request.masked_query, message_history=history(turns))
                async with stream as events:
                    async for ev in events:
                        if isinstance(ev, FunctionToolCallEvent):
                            args = (
                                ev.part.args
                                if isinstance(ev.part.args, str)
                                else json.dumps(ev.part.args or {})
                            )
                            yield _event(tool_started=pb.ToolStarted(name=ev.part.tool_name, args_json=args))
                        elif isinstance(ev, FunctionToolResultEvent):
                            content = getattr(ev.part, "content", None)
                            result = as_dict(content)
                            ok = not (result and result.get("status") in ("denied", "not_found"))
                            yield _event(
                                tool_result=pb.ToolResult(
                                    name=ev.part.tool_name or "",
                                    ok=ok,
                                    result_json=json.dumps(
                                        result if result is not None else str(content), default=str
                                    ),
                                )
                            )
                            if result and result.get("status") == "proposal":
                                proposals += 1
                                yield _event(
                                    proposal=pb.Proposal(
                                        proposal_id=result["proposal_id"],
                                        action=result["action"],
                                        summary_json=json.dumps(result.get("summary", {}), default=str),
                                    )
                                )
                            if result and result.get("status") == "denied":
                                status = "denied"
                        elif isinstance(ev, PartDeltaEvent) and isinstance(ev.delta, TextPartDelta):
                            yield _event(token=pb.Token(text=ev.delta.content_delta))
                        elif isinstance(ev, AgentRunResultEvent):
                            final_text = str(ev.result.output)
            if proposals:
                status = "proposal"
            yield _event(final=pb.Final(text=final_text, status=status))
        except TimeoutError:
            yield _event(error=pb.Error(message="The request took too long. Please try again."))
        except Exception as e:  # the gateway shows a generic message; details stay in logs
            log.exception("agent run failed: %s", e)
            yield _event(error=pb.Error(message="The assistant couldn't complete that request."))

    async def ExecuteConfirmed(
        self, request: pb.ConfirmRequest, context: grpc.aio.ServicerContext
    ) -> AsyncIterator[pb.AgentEvent]:
        s = get_settings()
        async with await psycopg.AsyncConnection.connect(s.database_url) as conn:
            row = await (
                await conn.execute(
                    "SELECT action, payload_canonical FROM action_confirmations"
                    " WHERE id = %s AND user_id = %s",
                    (request.proposal_id, request.claims.sub),
                )
            ).fetchone()
        if row is None:
            yield _event(error=pb.Error(message="This confirmation is invalid or has expired."))
            return
        action, payload_text = row
        # Parse the exact canonical text that was hashed, so the MCP server recomputes the same hash.
        args = {**json.loads(payload_text), "confirmation_token": request.confirmation_token}
        yield _event(tool_started=pb.ToolStarted(name=action, args_json=payload_text))
        result = await _call_mcp(action, args, request.obo_token)
        ok = result.get("status") == "executed"
        yield _event(
            tool_result=pb.ToolResult(name=action, ok=ok, result_json=json.dumps(result, default=str))
        )
        yield _event(
            final=pb.Final(
                text=_summary(action, result), status="done" if ok else result.get("status", "failed")
            )
        )

    async def CallTool(self, request: pb.ToolRequest, context: grpc.aio.ServicerContext) -> pb.ToolResponse:
        if request.name not in UI_TOOLS:
            return pb.ToolResponse(
                result_json=json.dumps({"status": "denied", "message": "Unknown tool."}), ok=False
            )
        args = json.loads(request.args_json or "{}")
        args.pop("confirmation_token", None)  # UI calls can only ever propose; execution needs /confirm
        result = await _call_mcp(request.name, args, request.obo_token)
        ok = result.get("status") not in ("denied", "not_found", "failed")
        return pb.ToolResponse(result_json=json.dumps(result, default=str), ok=ok)


# Tools the UI may call directly. Writes here only produce proposals (token stripped above).
UI_TOOLS = {
    "search_vendors",
    "get_vendor",
    "get_budget",
    "get_spend_summary",
    "list_my_requests",
    "get_purchase_request",
    "get_approval_chain",
    "list_pending_approvals",
    "approve_request",
    "reject_request",
    "submit_purchase_request",
    "generate_po_pdf",
}
NOTIFY_TOOLS = {"send_email", "post_chat_message", "generate_po_pdf"}


async def _call_mcp(name: str, args: dict[str, Any], obo_token: str) -> dict[str, Any]:
    s = get_settings()
    url = s.mcp_notify_url if name in NOTIFY_TOOLS else s.mcp_procurement_url
    try:
        client = Client(StreamableHttpTransport(url, headers={"Authorization": f"Bearer {obo_token}"}))
        async with client:
            res = await client.call_tool(name, args, raise_on_error=False)
    except Exception as e:
        log.exception("MCP call %s failed: %s", name, e)
        return {"status": "failed", "message": "That service is temporarily unavailable."}
    if res.is_error:
        return {"status": "failed", "message": "The request couldn't be completed."}
    result: Any = res.structured_content
    if result is None:
        # Unstructured output: one text block per item. Parse every block, not just the first.
        blocks = [_parse(getattr(c, "text", "")) for c in res.content]
        result = blocks[0] if len(blocks) == 1 else blocks
    if isinstance(result, dict) and set(result) == {"result"}:
        result = result["result"]  # MCP wraps non-object (e.g. list) returns as {"result": ...}
    return result if isinstance(result, dict) else {"status": "ok", "items": result}


def _parse(text: str) -> Any:
    try:
        return json.loads(text)
    except ValueError:
        return text


def _summary(action: str, r: dict[str, Any]) -> str:
    if r.get("status") != "executed":
        return str(r.get("message", "The action couldn't be completed."))
    pr = r.get("pr_id", "")
    if action == "create_purchase_request":
        return f"Created {pr}. Status: {r.get('pr_status')}."
    if action in ("approve_request", "reject_request"):
        return f"{pr} {r.get('decision')}. Status: {r.get('pr_status')}."
    return f"Done ({action})."


async def serve() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    server = grpc.aio.server()
    pb_grpc.add_BrainServicer_to_server(Brain(), server)
    bind = get_settings().brain_bind
    server.add_insecure_port(bind)  # internal network only; never published
    await server.start()
    log.info("brain gRPC listening on %s", bind)
    await server.wait_for_termination()


if __name__ == "__main__":
    asyncio.run(serve())
