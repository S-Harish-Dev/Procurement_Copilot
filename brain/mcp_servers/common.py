"""Shared plumbing for the MCP servers: identity from the OBO token, the service singleton, and
uniform tool results.

Every tool call is authenticated independently: the agent forwards a 60 s RS256 OBO token in the
`Authorization` header; the server verifies its signature with the gateway's public key (headers
are client-supplied, the signature is what makes them trustworthy) and then asks OPA itself —
the MCP layer re-checks policy instead of trusting that the gateway already did.
"""

import logging
import os
from typing import Any

import httpx
from mcp.server.mcpserver import Context
from mcp.server.transport_security import TransportSecuritySettings
from psycopg_pool import AsyncConnectionPool

from common.security import AuthError, Identity, bearer, verify_obo
from common.settings import get_settings
from procurement.notifier import Notifier
from procurement.service import Denied, NotFound, ProcurementService

log = logging.getLogger(__name__)
_service: ProcurementService | None = None


async def service() -> ProcurementService:
    global _service
    if _service is None:
        pool = AsyncConnectionPool(get_settings().database_url, min_size=1, max_size=8, open=False)
        await pool.open()
        _service = ProcurementService(pool, httpx.AsyncClient(timeout=3), Notifier(pool))
    return _service


def identity(ctx: Context[Any, Any]) -> Identity:
    try:
        return verify_obo(bearer(ctx.headers))
    except AuthError as e:
        log.warning("MCP call rejected: %s", e)
        raise PermissionError("unauthenticated") from e


def denied(message: str) -> dict[str, Any]:
    return {"status": "denied", "message": message}


async def guarded(coro: Any) -> Any:
    """Turn expected business outcomes into structured results the agent can relay."""
    try:
        return await coro
    except Denied as e:
        return denied(str(e))
    except NotFound as e:
        return {"status": "not_found", "message": str(e)}


def transport_security() -> TransportSecuritySettings:
    # DNS-rebinding protection stays on; allow the compose service names and localhost.
    hosts = os.environ.get("MCP_ALLOWED_HOSTS", "127.0.0.1:*,localhost:*,mcp-procurement:*,mcp-notify:*")
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[h.strip() for h in hosts.split(",") if h.strip()],
        allowed_origins=[],
    )
