"""MCP server `acme-procurement` (Streamable HTTP, :8101/mcp) — the procurement tools.

Read tools return data the caller may see (OPA-checked). Write tools are two-phase: called
without `confirmation_token` they return `{"status": "proposal", ...}` and change nothing; only a
one-time token minted by the gateway after a human clicks Confirm makes them execute.

    uv run python -m mcp_servers.procurement.server
"""

import os
from typing import Any

import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from pydantic import BaseModel, Field

from mcp_servers.common import guarded, identity, service, transport_security

mcp = MCPServer(
    name="acme-procurement",
    instructions=(
        "Acme procurement tools. Write tools (create/submit/approve/reject) never act directly: they "
        "return a proposal the user must confirm in the UI. Never claim an action happened unless a "
        "result says status=executed."
    ),
)


class LineItem(BaseModel):
    sku: str = Field(description="Catalog SKU or a short code")
    desc: str = Field(description="What is being bought")
    qty: int = Field(gt=0)
    unit_price: float = Field(ge=0, description="USD per unit")


@mcp.tool()
async def search_vendors(query: str, ctx: Context[Any, Any], category: str | None = None) -> Any:
    """Find vendors by name or category (e.g. 'cloud', 'IT Hardware')."""
    svc, user = await service(), identity(ctx)
    return await guarded(svc.search_vendors(user, query, category))


@mcp.tool()
async def get_vendor(vendor_id: str, ctx: Context[Any, Any]) -> Any:
    """Vendor details. Contact and bank details are masked unless your role may see them."""
    svc, user = await service(), identity(ctx)
    return await guarded(svc.get_vendor(user, vendor_id))


@mcp.tool()
async def get_budget(ctx: Context[Any, Any], department: str | None = None) -> Any:
    """FY2026 budget (allocated, committed, spent, remaining). Defaults to your department."""
    svc, user = await service(), identity(ctx)
    return await guarded(svc.get_budget(user, department))


@mcp.tool()
async def get_spend_summary(ctx: Context[Any, Any], department: str | None = None) -> Any:
    """FY2026 budget plus monthly spend and top vendors (approved + ordered). Defaults to your department."""
    svc, user = await service(), identity(ctx)
    return await guarded(svc.get_spend_summary(user, department))


@mcp.tool()
async def list_my_requests(ctx: Context[Any, Any], status: str | None = None) -> Any:
    """Your purchase requests, newest first. Optional status filter (e.g. pending_manager)."""
    svc, user = await service(), identity(ctx)
    return await guarded(svc.list_my_requests(user, status))


@mcp.tool()
async def get_purchase_request(pr_id: str, ctx: Context[Any, Any]) -> Any:
    """A purchase request with line items and approval history (if you may view it)."""
    svc, user = await service(), identity(ctx)
    return await guarded(svc.get_purchase_request(user, pr_id))


@mcp.tool()
async def get_approval_chain(pr_id: str, ctx: Context[Any, Any]) -> Any:
    """Who must approve a request, and where it is now."""
    svc, user = await service(), identity(ctx)
    return await guarded(svc.get_approval_chain(user, pr_id))


@mcp.tool()
async def list_pending_approvals(ctx: Context[Any, Any]) -> Any:
    """Requests waiting for your approval (managers, finance, admins)."""
    svc, user = await service(), identity(ctx)
    return await guarded(svc.list_pending_approvals(user))


async def _write(ctx: Context[Any, Any], action: str, payload: dict[str, Any], token: str | None) -> Any:
    svc, user = await service(), identity(ctx)
    if token is None:
        return await guarded(svc.propose(user, action, payload))
    return await guarded(svc.execute(user, action, payload, token))


@mcp.tool()
async def create_purchase_request(
    vendor_id: str,
    items: list[LineItem],
    justification: str,
    ctx: Context[Any, Any],
    submit: bool = True,
    confirmation_token: str | None = None,
) -> Any:
    """Create (and by default submit) a purchase request for your department. Returns a proposal."""
    payload = {
        "vendor_id": vendor_id,
        "items": [i.model_dump() for i in items],
        "justification": justification,
        "submit": submit,
    }
    return await _write(ctx, "create_purchase_request", payload, confirmation_token)


@mcp.tool()
async def submit_purchase_request(
    pr_id: str, ctx: Context[Any, Any], confirmation_token: str | None = None
) -> Any:
    """Submit one of your draft requests for approval. Returns a proposal."""
    return await _write(ctx, "submit_purchase_request", {"pr_id": pr_id}, confirmation_token)


@mcp.tool()
async def approve_request(
    pr_id: str, comment: str, ctx: Context[Any, Any], confirmation_token: str | None = None
) -> Any:
    """Approve a request waiting at your level. Returns a proposal."""
    return await _write(ctx, "approve_request", {"pr_id": pr_id, "comment": comment}, confirmation_token)


@mcp.tool()
async def reject_request(
    pr_id: str, comment: str, ctx: Context[Any, Any], confirmation_token: str | None = None
) -> Any:
    """Reject a request waiting at your level. Returns a proposal."""
    return await _write(ctx, "reject_request", {"pr_id": pr_id, "comment": comment}, confirmation_token)


def app() -> Any:
    # Stateless: no MCP session state on the server, so replicas can be added behind a balancer.
    return mcp.streamable_http_app(
        stateless_http=True, json_response=True, transport_security=transport_security()
    )


if __name__ == "__main__":
    uvicorn.run(
        app(),
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_PORT", "8101")),
        log_level="info",
    )
