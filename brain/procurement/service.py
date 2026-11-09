"""Procurement business logic: the purchase-request state machine, approvals, budgets.

One service layer used by the MCP tools. Rules are enforced at three levels:
1. OPA (`policies/authz.rego`) decides *who may do what* (thresholds, self-approval, departments);
2. this layer applies it transactionally with row locks (`SELECT ... FOR UPDATE`);
3. Postgres triggers/constraints are the backstop (legal transitions, no self-approval, budgets).

Writes are two-phase: `propose()` validates with the same checks (a user is never asked to
confirm something that will fail) and stores a proposal; `execute()` runs it only with a valid
one-time confirmation token, inside the same transaction that marks the token used.
"""

import logging
import uuid
from decimal import Decimal
from typing import Any

import httpx
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from common.crypto import decrypt
from common.security import (
    AuthError,
    ConfirmationGrant,
    Identity,
    canonical_json,
    sha256_hex,
    verify_confirmation,
)
from common.settings import get_settings
from procurement.notifier import Notifier

log = logging.getLogger(__name__)
FISCAL_YEAR = 2026
PROPOSAL_TTL_MINUTES = 5
WRITE_ACTIONS = {"create_purchase_request", "submit_purchase_request", "approve_request", "reject_request"}


class Denied(Exception):
    """A legitimate policy denial. The message is meant for the user."""


class NotFound(Exception):
    pass


def _money(x: Any) -> str:
    return f"USD {Decimal(str(x)):,.2f}"


def _items(rows: list[dict[str, Any]]) -> dict[str, Any]:
    # Tools always return an object: MCP clients get one structured result, not one block per row.
    return {"items": rows, "count": len(rows)}


def _jsonable(row: dict[str, Any]) -> dict[str, Any]:
    out = {}
    for k, v in row.items():
        if isinstance(v, Decimal):
            out[k] = float(v)
        elif hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        else:
            out[k] = v
    return out


class ProcurementService:
    def __init__(self, pool: AsyncConnectionPool, opa: httpx.AsyncClient, notifier: Notifier) -> None:
        self.pool, self.opa, self.notifier = pool, opa, notifier

    # ------------------------------------------------------------------ policy
    async def decide(self, user: Identity, action: str, resource: dict[str, Any]) -> dict[str, Any]:
        r = await self.opa.post(
            f"{get_settings().opa_url}/v1/data/acme/authz",
            json={"input": {"user": user.opa_user(), "action": action, "resource": resource}},
        )
        r.raise_for_status()
        result: dict[str, Any] | None = r.json().get("result")
        if result is None:
            # Fail closed: no policy loaded means no permission.
            raise Denied("This action is temporarily unavailable. Please try again shortly.")
        return result

    async def allowed(self, user: Identity, action: str, resource: dict[str, Any]) -> bool:
        try:
            return bool((await self.decide(user, action, resource))["allow"])
        except httpx.HTTPError:
            return False  # OPA down -> fail closed

    async def require_tool(self, user: Identity, tool: str) -> None:
        if not await self.allowed(user, "call_tool", {"type": "tool", "name": tool}):
            raise Denied("You don't have access to that action.")

    # ------------------------------------------------------------------ reads
    async def search_vendors(self, user: Identity, query: str, category: str | None = None) -> dict[str, Any]:
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """SELECT id, name, category, status, risk_rating, contact_email_masked
                   FROM vendors WHERE (lower(name) LIKE %s OR lower(category) LIKE %s)
                   AND (%s::text IS NULL OR category = %s) ORDER BY name LIMIT 10""",
                (f"%{query.lower()}%", f"%{query.lower()}%", category, category),
            )
            return _items([_jsonable(r) for r in await cur.fetchall()])

    async def get_vendor(self, user: Identity, vendor_id: str) -> dict[str, Any]:
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute("SELECT * FROM vendors WHERE id = %s", (vendor_id,))
            v = await cur.fetchone()
        if v is None:
            raise NotFound(f"No vendor {vendor_id}.")
        out = {
            k: v[k]
            for k in (
                "id",
                "name",
                "category",
                "status",
                "risk_rating",
                "contact_email_masked",
                "iban_masked",
            )
        }
        if await self.allowed(user, "view_pii", {}):
            # Finance/admin only: decrypt; AAD binds each value to its row and column.
            for col in ("contact_name", "contact_email", "phone", "iban", "tax_id"):
                out[col] = decrypt(bytes(v[f"{col}_enc"]), aad=f"vendors:{vendor_id}:{col}".encode())
        return _jsonable(out)

    async def get_budget(self, user: Identity, department: str | None = None) -> dict[str, Any]:
        dept = department or user.dept
        if not await self.allowed(user, "view_budget", {"dept": dept}):
            raise Denied(f"You can view your own department's budget ({user.dept}) only.")
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """SELECT department_id AS department, fiscal_year, allocated, committed, spent,
                          allocated - committed - spent AS remaining
                   FROM budgets WHERE department_id = %s AND fiscal_year = %s""",
                (dept, FISCAL_YEAR),
            )
            b = await cur.fetchone()
        if b is None:
            raise NotFound(f"No FY{FISCAL_YEAR} budget for {dept}.")
        return _jsonable(b)

    async def get_spend_summary(self, user: Identity, department: str | None = None) -> dict[str, Any]:
        """Budget plus where the money went: FY monthly spend and top vendors. "Spent" here means
        approved or ordered requests, the same requests that commit or consume budget."""
        budget = await self.get_budget(user, department)  # authorizes view_budget for the department
        dept = budget["department"]
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """SELECT to_char(date_trunc('month', created_at), 'YYYY-MM') AS month, sum(total) AS amount
                   FROM purchase_requests
                   WHERE department_id = %s AND status IN ('approved', 'ordered')
                     AND extract(year FROM created_at) = %s
                   GROUP BY 1 ORDER BY 1""",
                (dept, FISCAL_YEAR),
            )
            monthly = [_jsonable(r) for r in await cur.fetchall()]
            await cur.execute(
                """SELECT v.id AS vendor_id, v.name AS vendor, sum(pr.total) AS amount
                   FROM purchase_requests pr JOIN vendors v ON v.id = pr.vendor_id
                   WHERE pr.department_id = %s AND pr.status IN ('approved', 'ordered')
                     AND extract(year FROM pr.created_at) = %s
                   GROUP BY 1, 2 ORDER BY 3 DESC LIMIT 8""",
                (dept, FISCAL_YEAR),
            )
            top = [_jsonable(r) for r in await cur.fetchall()]
        return {**budget, "monthly": monthly, "top_vendors": top}

    async def list_my_requests(self, user: Identity, status: str | None = None) -> dict[str, Any]:
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                """SELECT pr.id, v.name AS vendor, pr.total, pr.status, pr.created_at
                   FROM purchase_requests pr JOIN vendors v ON v.id = pr.vendor_id
                   WHERE pr.requester_id = %s AND (%s::text IS NULL OR pr.status::text = %s)
                   ORDER BY pr.created_at DESC LIMIT 20""",
                (user.sub, status, status),
            )
            return _items([_jsonable(r) for r in await cur.fetchall()])

    async def _load_pr(self, cur: Any, pr_id: str, lock: bool = False) -> dict[str, Any]:
        await cur.execute(
            f"""SELECT pr.*, v.name AS vendor_name, v.status AS vendor_status, u.name AS requester_name
                FROM purchase_requests pr JOIN vendors v ON v.id = pr.vendor_id JOIN users u ON u.id = pr.requester_id
                WHERE pr.id = %s {"FOR UPDATE OF pr" if lock else ""}""",
            (pr_id,),
        )
        pr = await cur.fetchone()
        if pr is None:
            raise NotFound(f"No purchase request {pr_id}.")
        return dict(pr)

    async def get_purchase_request(self, user: Identity, pr_id: str) -> dict[str, Any]:
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            pr = await self._load_pr(cur, pr_id)
            if not await self.allowed(
                user, "view_pr", {"requester_id": pr["requester_id"], "dept": pr["department_id"]}
            ):
                raise NotFound(f"No purchase request {pr_id}.")  # same message as absent: no existence oracle
            await cur.execute(
                "SELECT line_no, sku, description, qty, unit_price FROM pr_line_items WHERE pr_id = %s ORDER BY line_no",
                (pr_id,),
            )
            items = [_jsonable(r) for r in await cur.fetchall()]
            await cur.execute(
                """SELECT a.level, a.decision, a.comment, a.decided_at, u.name AS approver
                   FROM approvals a JOIN users u ON u.id = a.approver_id WHERE a.pr_id = %s ORDER BY a.decided_at""",
                (pr_id,),
            )
            approvals = [_jsonable(r) for r in await cur.fetchall()]
        keep = (
            "id",
            "requester_name",
            "department_id",
            "vendor_name",
            "total",
            "currency",
            "justification",
            "status",
            "approval_chain",
            "created_at",
            "updated_at",
        )
        return {**_jsonable({k: pr[k] for k in keep}), "line_items": items, "approvals": approvals}

    async def get_approval_chain(self, user: Identity, pr_id: str) -> dict[str, Any]:
        pr = await self.get_purchase_request(user, pr_id)
        done = {a["level"]: a for a in pr["approvals"]}
        steps = [
            {
                "level": lvl,
                "state": done[lvl]["decision"]
                if lvl in done
                else ("waiting" if pr["status"] == f"pending_{lvl}" else "upcoming"),
                "by": done[lvl]["approver"] if lvl in done else None,
            }
            for lvl in pr["approval_chain"]
        ]
        return {"pr_id": pr_id, "status": pr["status"], "steps": steps}

    async def list_pending_approvals(self, user: Identity) -> dict[str, Any]:
        await self.require_tool(user, "list_pending_approvals")
        args: tuple[str, ...]
        if user.role == "manager":
            where, args = "pr.status = 'pending_manager' AND pr.department_id = %s", (user.dept,)
        elif user.role == "finance":
            where, args = "pr.status IN ('pending_finance', 'pending_manager')", ()
        else:
            where, args = "pr.status = 'pending_admin'", ()
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            await cur.execute(
                f"""SELECT pr.id, u.name AS requester, pr.department_id, v.name AS vendor, pr.total, pr.status, pr.created_at
                    FROM purchase_requests pr JOIN users u ON u.id = pr.requester_id JOIN vendors v ON v.id = pr.vendor_id
                    WHERE {where} AND pr.requester_id <> %s ORDER BY pr.total DESC LIMIT 25""",
                (*args, user.sub),
            )
            return _items([_jsonable(r) for r in await cur.fetchall()])

    # ------------------------------------------------------------------ proposals
    async def propose(self, user: Identity, action: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Validate exactly as `execute` would, then store a proposal the user must confirm."""
        if action not in WRITE_ACTIONS:
            raise Denied("Unknown action.")
        await self.require_tool(user, action)
        async with self.pool.connection() as conn, conn.cursor(row_factory=dict_row) as cur:
            summary = await self._dry_run(cur, user, action, payload)
            canonical = canonical_json(payload)
            proposal_id = f"prop-{uuid.uuid4().hex[:12]}"
            await cur.execute(
                """INSERT INTO action_confirmations (id, user_id, action, payload, payload_canonical, payload_hash,
                                                     summary, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, now() + make_interval(mins => %s))""",
                (
                    proposal_id,
                    user.sub,
                    action,
                    Jsonb(payload),
                    canonical,
                    sha256_hex(canonical),
                    Jsonb(summary),
                    PROPOSAL_TTL_MINUTES,
                ),
            )
            await self._audit(cur, user, "propose", proposal_id, {"action": action})
        return {
            "status": "proposal",
            "proposal_id": proposal_id,
            "action": action,
            "summary": summary,
            "message": "Please review and confirm this action. It will not happen until you confirm.",
        }

    async def _dry_run(self, cur: Any, user: Identity, action: str, p: dict[str, Any]) -> dict[str, Any]:
        if action == "create_purchase_request":
            return await self._check_create(cur, user, p)
        pr = await self._load_pr(cur, p["pr_id"])
        if action == "submit_purchase_request":
            if pr["requester_id"] != user.sub or pr["status"] != "draft":
                raise Denied("Only your own draft requests can be submitted.")
            chain = (
                await self.decide(
                    user,
                    "create_pr",
                    {"amount": float(pr["total"]), "dept": pr["department_id"], "budget_remaining": 1e18},
                )
            )["approval_chain"]
            return {"pr_id": pr["id"], "total": float(pr["total"]), "approval_chain": chain}
        decision = "approved" if action == "approve_request" else "rejected"
        level = await self._check_decision(user, pr, decision)
        return {
            "pr_id": pr["id"],
            "requester": pr["requester_name"],
            "vendor": pr["vendor_name"],
            "total": float(pr["total"]),
            "level": level,
            "decision": decision,
            "approval_chain": pr["approval_chain"],
            "comment": p.get("comment", ""),
        }

    async def _remaining(self, cur: Any, dept: str, lock: bool = False) -> Decimal:
        await cur.execute(
            f"SELECT allocated - committed - spent AS remaining FROM budgets WHERE department_id = %s AND fiscal_year = %s"
            f"{' FOR UPDATE' if lock else ''}",
            (dept, FISCAL_YEAR),
        )
        row = await cur.fetchone()
        return Decimal(row["remaining"]) if row else Decimal(0)

    async def _check_create(self, cur: Any, user: Identity, p: dict[str, Any]) -> dict[str, Any]:
        items = p.get("items") or []
        if not items:
            raise Denied("Add at least one line item.")
        for it in items:
            if int(it["qty"]) <= 0 or Decimal(str(it["unit_price"])) < 0:
                raise Denied("Quantities must be positive and prices non-negative.")
        total = sum(Decimal(str(it["unit_price"])) * int(it["qty"]) for it in items)
        await cur.execute("SELECT id, name, status FROM vendors WHERE id = %s", (p["vendor_id"],))
        vendor = await cur.fetchone()
        if vendor is None:
            raise Denied(f"Vendor {p['vendor_id']} doesn't exist. Search vendors first.")
        if vendor["status"] != "active":
            raise Denied(f"{vendor['name']} is {vendor['status']}; new orders need an active vendor.")
        remaining = await self._remaining(cur, user.dept)
        d = await self.decide(
            user,
            "create_pr",
            {"amount": float(total), "dept": user.dept, "budget_remaining": float(remaining)},
        )
        if not d["allow"]:
            if "exceeds_budget" in d["reasons"]:
                raise Denied(
                    f"This request ({_money(total)}) exceeds the {user.dept} department's remaining "
                    f"FY{FISCAL_YEAR} budget ({_money(remaining)})."
                )
            raise Denied("You can't create this request.")
        return {
            "vendor_id": vendor["id"],
            "vendor": vendor["name"],
            "items": items,
            "total": float(total),
            "department": user.dept,
            "budget_remaining_before": float(remaining),
            "budget_remaining_after": float(remaining - total),
            "approval_chain": d["approval_chain"],
            "justification": p.get("justification", ""),
            "submit": bool(p.get("submit", True)),
        }

    async def _check_decision(self, user: Identity, pr: dict[str, Any], decision: str) -> str:
        if not str(pr["status"]).startswith("pending_"):
            raise Denied(f"{pr['id']} is {pr['status']}, not waiting for approval.")
        level = str(pr["status"]).removeprefix("pending_")
        action = "approve_pr" if decision == "approved" else "reject_pr"
        d = await self.decide(
            user,
            action,
            {
                "amount": float(pr["total"]),
                "dept": pr["department_id"],
                "level": level,
                "requester_id": pr["requester_id"],
            },
        )
        if d["allow"]:
            return level
        reasons = set(d["reasons"])
        if "self_approval" in reasons:
            raise Denied("You can't approve or reject your own request.")
        if "exceeds_approval_limit" in reasons:
            # Escalate for real: Finance covers the manager level (policy), so tell them now.
            await self.notifier.email(
                "finance-team@acme.example",
                f"Escalated approval: {pr['id']}",
                f"{user.name}'s approval limit ({_money(user.approval_limit)}) is below {pr['id']} "
                f"({_money(pr['total'])}). Finance may approve it at the manager level.",
                pr["id"],
            )
            raise Denied(
                f"{pr['id']} ({_money(pr['total'])}) exceeds your approval limit of "
                f"{_money(user.approval_limit)}; it has been routed to Finance."
            )
        raise Denied(f"{pr['id']} isn't waiting for your approval.")

    # ------------------------------------------------------------------ execution
    async def execute(
        self, user: Identity, action: str, payload: dict[str, Any], token: str
    ) -> dict[str, Any]:
        try:
            grant = verify_confirmation(token)
        except AuthError as e:
            log.warning("confirmation rejected for %s: %s", user.sub, e)
            raise Denied("This confirmation is invalid or has expired. Please ask again.") from e
        canonical = canonical_json(payload)
        if grant.user_id != user.sub or grant.payload_hash != sha256_hex(canonical):
            raise Denied("This confirmation doesn't match the proposed action.")
        notify: list[tuple[str, str, str, str]] = []
        async with (
            self.pool.connection() as conn,
            conn.transaction(),
            conn.cursor(row_factory=dict_row) as cur,
        ):
            replay = await self._consume(cur, user, action, grant, token)
            if replay is not None:
                return replay  # idempotent: same token twice -> same result, action runs once
            if action == "create_purchase_request":
                result = await self._create(cur, user, payload, grant.proposal_id, notify)
            elif action == "submit_purchase_request":
                pr = await self._load_pr(cur, payload["pr_id"], lock=True)
                result = await self._submit(cur, user, pr, notify)
            else:
                result = await self._decide(
                    cur,
                    user,
                    payload["pr_id"],
                    "approved" if action == "approve_request" else "rejected",
                    payload.get("comment", ""),
                    notify,
                )
            await cur.execute(
                "UPDATE action_confirmations SET status = 'executed', result = %s, executed_at = now() WHERE id = %s",
                (Jsonb(result), grant.proposal_id),
            )
            await self._audit(
                cur, user, action, result.get("pr_id"), {"proposal_id": grant.proposal_id, **result}
            )
        for to, subject, body, pr_id in notify:  # after commit: mail never precedes the data it describes
            await self.notifier.email(to, subject, body, pr_id)
        return result

    async def _consume(
        self, cur: Any, user: Identity, action: str, grant: ConfirmationGrant, token: str
    ) -> dict[str, Any] | None:
        await cur.execute("SELECT * FROM action_confirmations WHERE id = %s FOR UPDATE", (grant.proposal_id,))
        row = await cur.fetchone()
        if (
            row is None
            or row["user_id"] != user.sub
            or row["action"] != action
            or row["payload_hash"] != grant.payload_hash
        ):
            raise Denied("This confirmation doesn't match the proposed action.")
        if row["token_hash"] != sha256_hex(token):
            raise Denied("This confirmation is invalid or has expired. Please ask again.")
        if row["used_at"] is not None:
            if row["status"] == "executed" and row["result"] is not None:
                return {**row["result"], "replayed": True}
            raise Denied("This confirmation has already been used.")
        await cur.execute(
            "UPDATE action_confirmations SET used_at = now() WHERE id = %s", (grant.proposal_id,)
        )
        return None

    async def _create(
        self, cur: Any, user: Identity, p: dict[str, Any], idem: str, notify: list[Any]
    ) -> dict[str, Any]:
        await self._remaining(cur, user.dept, lock=True)  # serialize concurrent creates per department
        summary = await self._check_create(cur, user, p)
        await cur.execute(
            """INSERT INTO purchase_requests (requester_id, department_id, vendor_id, total, justification, idempotency_key)
               VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT (idempotency_key) DO NOTHING RETURNING id""",
            (user.sub, user.dept, p["vendor_id"], summary["total"], p.get("justification", ""), idem),
        )
        row = await cur.fetchone()
        if row is None:  # same idempotency key already executed
            await cur.execute("SELECT id FROM purchase_requests WHERE idempotency_key = %s", (idem,))
            row = await cur.fetchone()
            return {"status": "executed", "pr_id": row["id"], "replayed": True}
        pr_id = row["id"]
        for n, it in enumerate(p["items"], start=1):
            await cur.execute(
                "INSERT INTO pr_line_items (pr_id, line_no, sku, description, qty, unit_price) VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    pr_id,
                    n,
                    it.get("sku", f"ITEM-{n}"),
                    it.get("desc") or it.get("description", ""),
                    int(it["qty"]),
                    it["unit_price"],
                ),
            )
        if not p.get("submit", True):
            return {"status": "executed", "pr_id": pr_id, "pr_status": "draft", "total": summary["total"]}
        pr = await self._load_pr(cur, pr_id, lock=True)
        return await self._submit(cur, user, pr, notify)

    async def _submit(
        self, cur: Any, user: Identity, pr: dict[str, Any], notify: list[Any]
    ) -> dict[str, Any]:
        if pr["requester_id"] != user.sub or pr["status"] != "draft":
            raise Denied("Only your own draft requests can be submitted.")
        remaining = await self._remaining(cur, pr["department_id"], lock=True)
        d = await self.decide(
            user,
            "create_pr",
            {"amount": float(pr["total"]), "dept": pr["department_id"], "budget_remaining": float(remaining)},
        )
        if not d["allow"]:
            raise Denied(
                f"This request exceeds the remaining {pr['department_id']} budget ({_money(remaining)})."
            )
        chain: list[str] = d["approval_chain"]
        if not chain:
            status = "approved"
            await self._commit_budget(cur, pr)
            notify.append(
                (
                    f"{pr['requester_id']}@acme.example",
                    f"{pr['id']} approved automatically",
                    f"Your request {pr['id']} ({_money(pr['total'])}) is within the auto-approval limit and is approved.",
                    pr["id"],
                )
            )
        else:
            status = f"pending_{chain[0]}"
            for email in await self._approver_emails(cur, chain[0], pr["department_id"], pr["requester_id"]):
                notify.append(
                    (
                        email,
                        f"Approval needed: {pr['id']}",
                        f"{pr['requester_name']} requested {_money(pr['total'])} from {pr['vendor_name']}. "
                        f"Approval chain: {' -> '.join(chain)}.",
                        pr["id"],
                    )
                )
        await cur.execute(
            "UPDATE purchase_requests SET status = %s, approval_chain = %s WHERE id = %s",
            (status, chain, pr["id"]),
        )
        return {
            "status": "executed",
            "pr_id": pr["id"],
            "pr_status": status,
            "approval_chain": chain,
            "total": float(pr["total"]),
        }

    async def _decide(
        self, cur: Any, user: Identity, pr_id: str, decision: str, comment: str, notify: list[Any]
    ) -> dict[str, Any]:
        pr = await self._load_pr(cur, pr_id, lock=True)
        level = await self._check_decision(user, pr, decision)
        await cur.execute(
            "INSERT INTO approvals (pr_id, approver_id, level, decision, comment) VALUES (%s, %s, %s, %s, %s)",
            (pr_id, user.sub, level, decision, comment),
        )
        chain: list[str] = list(pr["approval_chain"])
        requester = f"{pr['requester_id']}@acme.example"
        if decision == "rejected":
            status = "rejected"
            notify.append(
                (
                    requester,
                    f"{pr_id} rejected",
                    f"{user.name} rejected {pr_id}: {comment or 'no comment'}",
                    pr_id,
                )
            )
        elif chain.index(level) + 1 < len(chain):
            nxt = chain[chain.index(level) + 1]
            status = f"pending_{nxt}"
            for email in await self._approver_emails(cur, nxt, pr["department_id"], pr["requester_id"]):
                notify.append(
                    (
                        email,
                        f"Approval needed: {pr_id}",
                        f"{pr_id} ({_money(pr['total'])}) was approved at {level} level and now needs {nxt} approval.",
                        pr_id,
                    )
                )
        else:
            status = "approved"
            await self._commit_budget(cur, pr)
            notify.append(
                (requester, f"{pr_id} approved", f"{pr_id} ({_money(pr['total'])}) is fully approved.", pr_id)
            )
        await cur.execute("UPDATE purchase_requests SET status = %s WHERE id = %s", (status, pr_id))
        return {
            "status": "executed",
            "pr_id": pr_id,
            "decision": decision,
            "level": level,
            "pr_status": status,
        }

    async def _commit_budget(self, cur: Any, pr: dict[str, Any]) -> None:
        try:
            await cur.execute(
                "UPDATE budgets SET committed = committed + %s WHERE department_id = %s AND fiscal_year = %s",
                (pr["total"], pr["department_id"], FISCAL_YEAR),
            )
        except Exception as e:  # CHECK (committed + spent <= allocated)
            raise Denied(f"Approving {pr['id']} would exceed the {pr['department_id']} budget.") from e

    async def _approver_emails(self, cur: Any, level: str, dept: str, requester: str) -> list[str]:
        if level == "manager":
            await cur.execute(
                "SELECT email FROM users WHERE role = 'manager' AND department_id = %s AND id <> %s",
                (dept, requester),
            )
            rows = await cur.fetchall()
            if rows:
                return [r["email"] for r in rows]
            level = "finance"  # no manager available -> Finance covers the manager level
        await cur.execute(
            "SELECT email FROM users WHERE role = %s::user_role AND id <> %s", (level, requester)
        )
        return [r["email"] for r in await cur.fetchall()]

    async def _audit(
        self, cur: Any, user: Identity, action: str, target: str | None, details: dict[str, Any]
    ) -> None:
        await cur.execute(
            "INSERT INTO audit_log (actor_id, action, target, details) VALUES (%s, %s, %s, %s)",
            (user.sub, action, target, Jsonb(details)),
        )
