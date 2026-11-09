"""Load data/synthetic/*.json into Postgres. Idempotent (ON CONFLICT DO NOTHING).

Vendor PII is encrypted here with AES-GCM; the associated data binds each
ciphertext to (vendor id, column) so a value can't be copied onto another row.

    uv run python -m synthetic.seed
"""

import json
import re
from typing import Any

import psycopg

from common.crypto import encrypt
from common.settings import get_settings


def mask_email(email: str) -> str:
    local, _, domain = email.partition("@")
    return f"{local[0]}•••@{domain}"


def mask_iban(iban: str) -> str:
    compact = re.sub(r"\s", "", iban)
    return f"{compact[:4]} •••• •••• {compact[-4:]}"


def _enc(value: str, vendor_id: str, column: str) -> bytes:
    return encrypt(value, aad=f"vendors:{vendor_id}:{column}".encode())


def _load(name: str) -> Any:
    return json.loads((get_settings().data_dir / "synthetic" / name).read_text())


def main() -> None:
    org = _load("org.json")
    vendors = _load("vendors.json")
    contracts = _load("contracts.json")
    prs = _load("purchase_requests.json")

    with psycopg.connect(get_settings().database_url) as conn, conn.cursor() as cur:
        cur.executemany(
            "INSERT INTO departments (id, name) VALUES (%(id)s, %(name)s) ON CONFLICT DO NOTHING",
            org["departments"],
        )
        cur.executemany(
            """INSERT INTO users (id, email, name, role, department_id, spend_limit, approval_limit)
               VALUES (%(id)s, %(email)s, %(name)s, %(role)s, %(department_id)s, %(spend_limit)s,
                       %(approval_limit)s) ON CONFLICT DO NOTHING""",
            org["users"],
        )
        cur.executemany(
            """INSERT INTO budgets (department_id, fiscal_year, allocated, committed, spent)
               VALUES (%(department_id)s, %(fiscal_year)s, %(allocated)s, %(committed)s, %(spent)s)
               ON CONFLICT DO NOTHING""",
            org["budgets"],
        )
        cur.executemany(
            """INSERT INTO vendors (id, name, category, status, risk_rating, contact_name_enc,
                   contact_email_enc, contact_email_masked, phone_enc, iban_enc, iban_masked, tax_id_enc)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
            [
                (
                    v["id"],
                    v["name"],
                    v["category"],
                    v["status"],
                    v["risk_rating"],
                    _enc(v["contact_name"], v["id"], "contact_name"),
                    _enc(v["contact_email"], v["id"], "contact_email"),
                    mask_email(v["contact_email"]),
                    _enc(v["phone"], v["id"], "phone"),
                    _enc(v["iban"], v["id"], "iban"),
                    mask_iban(v["iban"]),
                    _enc(v["tax_id"], v["id"], "tax_id"),
                )
                for v in vendors
            ],
        )
        cur.executemany(
            """INSERT INTO contracts (id, vendor_id, title, start_date, end_date, value, currency, doc_id)
               VALUES (%(id)s, %(vendor_id)s, %(title)s, %(start_date)s, %(end_date)s, %(value)s,
                       %(currency)s, %(doc_id)s) ON CONFLICT (id) DO UPDATE SET title = EXCLUDED.title""",
            contracts,
        )
        for pr in prs:
            cur.execute(
                """INSERT INTO purchase_requests (id, requester_id, department_id, vendor_id, total, currency,
                       justification, status, approval_chain, created_at, updated_at)
                   VALUES (%(id)s, %(requester_id)s, %(department_id)s, %(vendor_id)s, %(total)s,
                           %(currency)s, %(justification)s, %(status)s, %(approval_chain)s,
                           %(created_at)s, %(created_at)s)
                   ON CONFLICT DO NOTHING""",
                pr,
            )
            cur.executemany(
                """INSERT INTO pr_line_items (pr_id, line_no, sku, description, qty, unit_price)
                   VALUES (%s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                [
                    (pr["id"], li["line_no"], li["sku"], li["description"], li["qty"], li["unit_price"])
                    for li in pr["line_items"]
                ],
            )
            cur.executemany(
                """INSERT INTO approvals (pr_id, approver_id, level, decision, comment, decided_at)
                   VALUES (%s, %s, %s, %s, %s, %s::date + interval '1 day') ON CONFLICT DO NOTHING""",
                [
                    (pr["id"], a["approver_id"], a["level"], a["decision"], a["comment"], pr["created_at"])
                    for a in pr["approvals"]
                ],
            )
        # New PRs this year continue after the highest seeded number.
        cur.execute(
            """SELECT setval('pr_number_seq', COALESCE(MAX(split_part(id, '-', 3)::int), 0) + 1, false)
               FROM purchase_requests WHERE id LIKE 'PR-' || to_char(now(), 'YYYY') || '-%'"""
        )
        cur.execute(
            "INSERT INTO audit_log (actor_id, action, details) VALUES ('system', 'seed', %s)",
            (json.dumps({"users": len(org["users"]), "vendors": len(vendors), "prs": len(prs)}),),
        )
    print(
        f"seeded {len(org['users'])} users, {len(vendors)} vendors, "
        f"{len(contracts)} contracts, {len(prs)} PRs"
    )


if __name__ == "__main__":
    main()
