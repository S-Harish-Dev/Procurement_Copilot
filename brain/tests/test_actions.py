"""Integration tests for the action path against the real Postgres + OPA (skipped if not running).

Mirrors what the gateway does around the service: signs OBO tokens with the dev RS256 key and mints
one-time confirmation tokens (same format as gateway/crates/auth) after storing their hash.
"""

import base64
import hashlib
import hmac
import json
import time
import uuid
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Any

import httpx
import jwt
import psycopg
import pytest
import pytest_asyncio
from psycopg_pool import AsyncConnectionPool

from common.security import ISSUER, Identity, sha256_hex, verify_obo
from common.settings import REPO_ROOT, get_settings
from procurement.notifier import Notifier
from procurement.service import Denied, ProcurementService


def _db_up() -> bool:
    try:
        with psycopg.connect(get_settings().database_url, connect_timeout=2) as c:
            c.execute("SELECT 1 FROM action_confirmations LIMIT 1")
        httpx.get(f"{get_settings().opa_url}/health", timeout=2).raise_for_status()
        return True
    except Exception:
        return False


pytestmark = [pytest.mark.skipif(not _db_up(), reason="needs the running stack (Postgres+OPA)")]

PRIVATE = (
    (REPO_ROOT / "infra" / "keys" / "jwt-private.pem").read_text()
    if (REPO_ROOT / "infra/keys/jwt-private.pem").exists()
    else ""
)


def ident(user_id: str) -> Identity:
    with psycopg.connect(get_settings().database_url) as c:
        row = c.execute(
            "SELECT name, role::text, department_id, spend_limit::float8, approval_limit::float8 FROM users WHERE id=%s",
            (user_id,),
        ).fetchone()
    assert row is not None
    return Identity(user_id, row[0], row[1], row[2], row[3], row[4])


def obo(u: Identity) -> str:
    now = int(time.time())
    claims = {
        "sub": u.sub,
        "name": u.name,
        "role": u.role,
        "dept": u.dept,
        "spend_limit": u.spend_limit,
        "approval_limit": u.approval_limit,
        "typ": "obo",
        "iss": ISSUER,
        "aud": "mcp",
        "iat": now,
        "exp": now + 60,
    }
    return jwt.encode(claims, PRIVATE, algorithm="RS256")


def mint(proposal_id: str, user: Identity, key: bytes | None = None) -> str:
    """What the gateway's confirm endpoint does: checks the row, mints the token, stores its hash."""
    with psycopg.connect(get_settings().database_url) as c:
        ph = c.execute(
            "SELECT payload_hash FROM action_confirmations WHERE id=%s AND user_id=%s",
            (proposal_id, user.sub),
        ).fetchone()
        assert ph is not None
        grant = {
            "proposal_id": proposal_id,
            "user_id": user.sub,
            "payload_hash": ph[0],
            "exp": int(time.time()) + 300,
            "nonce": uuid.uuid4().hex,
        }
        body = json.dumps(grant).encode()
        k = key or base64.b64decode(get_settings().confirmation_hmac_key.get_secret_value())
        sig = hmac.new(k, body, hashlib.sha256).digest()
        token = (
            base64.urlsafe_b64encode(body).rstrip(b"=").decode()
            + "."
            + base64.urlsafe_b64encode(sig).rstrip(b"=").decode()
        )
        c.execute(
            "UPDATE action_confirmations SET token_hash=%s, status='confirmed' WHERE id=%s",
            (sha256_hex(token), proposal_id),
        )
    return token


@pytest_asyncio.fixture
async def svc() -> AsyncIterator[ProcurementService]:
    pool = AsyncConnectionPool(get_settings().database_url, min_size=1, max_size=4, open=False)
    await pool.open()
    async with httpx.AsyncClient(timeout=5) as opa:
        yield ProcurementService(pool, opa, Notifier(pool))
    await pool.close()


async def run(
    svc: ProcurementService, user: Identity, action: str, payload: dict[str, Any]
) -> dict[str, Any]:
    prop = await svc.propose(user, action, payload)
    assert prop["status"] == "proposal"
    return await svc.execute(user, action, json.loads(json.dumps(payload)), mint(prop["proposal_id"], user))


def active_vendor(offset: int = 0) -> str:
    with psycopg.connect(get_settings().database_url) as c:
        row = c.execute(
            "SELECT id FROM vendors WHERE status='active' ORDER BY id OFFSET %s LIMIT 1", (offset,)
        ).fetchone()
    assert row is not None
    return str(row[0])


def remaining(dept: str) -> Decimal:
    with psycopg.connect(get_settings().database_url) as c:
        r = c.execute(
            "SELECT allocated-committed-spent FROM budgets WHERE department_id=%s AND fiscal_year=2026",
            (dept,),
        ).fetchone()
    assert r is not None
    return Decimal(r[0])


def test_obo_roundtrip() -> None:
    u = ident("priya.sharma")
    assert verify_obo(obo(u)).sub == "priya.sharma"


@pytest.mark.asyncio
async def test_full_approval_chain_commits_budget_and_notifies(svc: ProcurementService) -> None:
    priya, james, elena = ident("priya.sharma"), ident("james.chen"), ident("elena.petrova")
    before = remaining("IT")
    payload = {
        "vendor_id": "V-0001",
        "items": [{"sku": "LAP-1", "desc": "Engineering laptops", "qty": 5, "unit_price": 3000.0}],
        "justification": "Team refresh",
        "submit": True,
    }
    created = await run(svc, priya, "create_purchase_request", payload)
    assert created["pr_status"] == "pending_manager" and created["approval_chain"] == ["manager", "finance"]
    pr = created["pr_id"]
    step1 = await run(svc, james, "approve_request", {"pr_id": pr, "comment": "ok"})
    assert step1["pr_status"] == "pending_finance"
    assert remaining("IT") == before, "budget is committed only on final approval"
    step2 = await run(svc, elena, "approve_request", {"pr_id": pr, "comment": "fine"})
    assert step2["pr_status"] == "approved"
    assert remaining("IT") == before - Decimal("15000.00")
    with psycopg.connect(get_settings().database_url) as c:
        mails = c.execute("SELECT count(*) FROM notifications WHERE pr_id=%s", (pr,)).fetchone()
        audit = c.execute("SELECT count(*) FROM audit_log WHERE target=%s", (pr,)).fetchone()
    assert mails is not None and mails[0] >= 3  # manager, finance, requester
    assert audit is not None and audit[0] >= 3


@pytest.mark.asyncio
async def test_self_approval_and_wrong_level_denied(svc: ProcurementService) -> None:
    james = ident("james.chen")
    created = await run(
        svc,
        james,
        "create_purchase_request",
        {
            "vendor_id": "V-0001",
            "items": [{"sku": "X", "desc": "Dock", "qty": 1, "unit_price": 2500.0}],
            "justification": "dock",
            "submit": True,
        },
    )
    with pytest.raises(Denied, match="your own request"):
        await svc.propose(james, "approve_request", {"pr_id": created["pr_id"], "comment": "me"})
    with pytest.raises(Denied):
        await svc.propose(
            ident("aisha.rahman"), "approve_request", {"pr_id": created["pr_id"], "comment": "other dept"}
        )


@pytest.mark.asyncio
async def test_over_limit_manager_is_routed_to_finance(svc: ProcurementService) -> None:
    noah, grace, elena = ident("noah.williams"), ident("grace.kim"), ident("elena.petrova")
    created = await run(
        svc,
        noah,
        "create_purchase_request",
        {
            "vendor_id": active_vendor(5),
            "items": [{"sku": "TR", "desc": "Training", "qty": 1, "unit_price": 8000.0}],
            "justification": "course",
            "submit": True,
        },
    )
    with pytest.raises(Denied, match="routed to Finance"):
        await svc.propose(grace, "approve_request", {"pr_id": created["pr_id"], "comment": "x"})
    done = await run(
        svc, elena, "approve_request", {"pr_id": created["pr_id"], "comment": "covering manager level"}
    )
    assert done["pr_status"] == "approved"


@pytest.mark.asyncio
async def test_token_is_single_use_bound_to_payload_and_unforgeable(svc: ProcurementService) -> None:
    priya = ident("priya.sharma")
    payload = {
        "vendor_id": "V-0001",
        "items": [{"sku": "M", "desc": "Mouse", "qty": 2, "unit_price": 30.0}],
        "justification": "mice",
        "submit": True,
    }
    prop = await svc.propose(priya, "create_purchase_request", payload)
    token = mint(prop["proposal_id"], priya)
    # Tampered payload (a bigger order) with a genuine token -> rejected.
    bigger = {**payload, "items": [{"sku": "M", "desc": "Mouse", "qty": 2000, "unit_price": 30.0}]}
    with pytest.raises(Denied):
        await svc.execute(priya, "create_purchase_request", bigger, token)
    first = await svc.execute(priya, "create_purchase_request", payload, token)
    assert first["pr_status"] == "approved"  # 60 USD: auto-approved
    again = await svc.execute(priya, "create_purchase_request", payload, token)
    assert again["replayed"] and again["pr_id"] == first["pr_id"], (
        "replay returns the same result, no second PR"
    )
    # Someone else's identity with the token -> rejected.
    with pytest.raises(Denied):
        await svc.execute(ident("marcus.lee"), "create_purchase_request", payload, token)
    # A token signed with a guessed key -> rejected.
    prop2 = await svc.propose(priya, "create_purchase_request", payload)
    forged = mint(prop2["proposal_id"], priya, key=b"x" * 32)
    with pytest.raises(Denied):
        await svc.execute(priya, "create_purchase_request", payload, forged)


@pytest.mark.asyncio
async def test_over_budget_and_inactive_vendor_denied_at_proposal(svc: ProcurementService) -> None:
    priya = ident("priya.sharma")
    with pytest.raises(Denied, match="exceeds the IT department's remaining"):
        await svc.propose(
            priya,
            "create_purchase_request",
            {
                "vendor_id": "V-0001",
                "items": [{"sku": "S", "desc": "Supercomputer", "qty": 1, "unit_price": 9e9}],
                "justification": "x",
                "submit": True,
            },
        )
    with psycopg.connect(get_settings().database_url) as c:
        row = c.execute("SELECT id FROM vendors WHERE status='suspended' LIMIT 1").fetchone()
    assert row is not None
    with pytest.raises(Denied, match="active vendor"):
        await svc.propose(
            priya,
            "create_purchase_request",
            {
                "vendor_id": row[0],
                "items": [{"sku": "S", "desc": "x", "qty": 1, "unit_price": 10.0}],
                "justification": "x",
                "submit": True,
            },
        )
