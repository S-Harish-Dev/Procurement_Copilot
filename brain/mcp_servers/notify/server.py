"""MCP server `acme-notify` (Streamable HTTP, :8102/mcp): e-mail (SMTP -> Mailpit in dev), an in-app
chat feed, and purchase-order PDFs. Same OBO authentication and OPA re-checks as procurement.

    uv run python -m mcp_servers.notify.server
"""

import os
from typing import Any

import uvicorn
from mcp.server.mcpserver import Context, MCPServer
from psycopg.rows import dict_row

from mcp_servers.common import denied, guarded, identity, service, transport_security
from procurement.po_pdf import render_po

mcp = MCPServer(
    name="acme-notify", instructions="Notifications and purchase-order documents for Acme procurement."
)

TEMPLATES = {
    "reminder": "Reminder: {pr} is waiting for your action. (sent by {sender})",
    "status_update": "Status update on {pr}: {status}. (sent by {sender})",
}


@mcp.tool()
async def send_email(
    template: str, pr_id: str, ctx: Context[Any, Any], to_user: str | None = None, to_role: str | None = None
) -> Any:
    """E-mail a colleague (to_user = user id) or a role (to_role = finance/admin) about a request.
    Only fixed templates ('reminder', 'status_update') are allowed — no free text, so the tool can't be
    turned into a spam or phishing channel."""
    svc, user = await service(), identity(ctx)
    if template not in TEMPLATES:
        return denied(f"Unknown template. Use one of: {', '.join(TEMPLATES)}.")
    await svc.require_tool(user, "send_email")
    pr = await guarded(svc.get_purchase_request(user, pr_id))  # must be allowed to see the request
    if pr.get("status") in ("denied", "not_found"):
        return pr
    async with svc.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        if to_user:
            await cur.execute("SELECT email FROM users WHERE id = %s", (to_user,))
        elif to_role in ("finance", "admin", "manager"):
            await cur.execute("SELECT email FROM users WHERE role = %s::user_role", (to_role,))
        else:
            return denied("Give to_user or to_role (finance, admin or manager).")
        recipients = [r["email"] for r in await cur.fetchall()]
    body = TEMPLATES[template].format(pr=pr_id, status=pr["status"], sender=user.name)
    sent = [r for r in recipients if await svc.notifier.email(r, f"[{template}] {pr_id}", body, pr_id)]
    return {"status": "executed", "sent_to": len(sent)}


@mcp.tool()
async def post_chat_message(channel: str, text: str, ctx: Context[Any, Any]) -> Any:
    """Post a short message to an in-app channel (e.g. 'procurement')."""
    svc, user = await service(), identity(ctx)
    await svc.require_tool(user, "post_chat_message")
    await svc.notifier.chat(channel, f"{user.name}: {text[:500]}")
    return {"status": "executed"}


@mcp.tool()
async def generate_po_pdf(pr_id: str, ctx: Context[Any, Any]) -> Any:
    """Create the purchase-order PDF for an approved request you may view."""
    svc, user = await service(), identity(ctx)
    await svc.require_tool(user, "generate_po_pdf")
    pr = await guarded(svc.get_purchase_request(user, pr_id))
    if pr.get("status") in ("denied", "not_found"):
        return pr
    if pr["status"] not in ("approved", "ordered"):
        return denied(f"{pr_id} is {pr['status']}; a purchase order needs an approved request.")
    path, digest = render_po(pr)
    async with svc.pool.connection() as conn:
        await conn.execute(
            """INSERT INTO po_documents (pr_id, path, sha256, generated_by) VALUES (%s, %s, %s, %s)
               ON CONFLICT (pr_id) DO UPDATE SET path = EXCLUDED.path, sha256 = EXCLUDED.sha256,
                   generated_by = EXCLUDED.generated_by, generated_at = now()""",
            (pr_id, path, digest, user.sub),
        )
    return {"status": "executed", "pr_id": pr_id, "download": f"/v1/files/po/{pr_id}"}


def app() -> Any:
    return mcp.streamable_http_app(
        stateless_http=True, json_response=True, transport_security=transport_security()
    )


if __name__ == "__main__":
    uvicorn.run(
        app(),
        host=os.environ.get("MCP_HOST", "127.0.0.1"),
        port=int(os.environ.get("MCP_PORT", "8102")),
        log_level="info",
    )
