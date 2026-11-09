"""Deterministic synthetic data for Acme Corp.

Writes JSON + markdown under data/synthetic/ (committed, so evals are stable):
  org.json                departments, users, budgets (committed/spent derived from PRs)
  vendors.json            200 vendors with fake PII (plaintext here; encrypted when seeded)
  contracts.json          30 Acme vendor contracts: metadata + the facts each text states (eval gold)
  purchase_requests.json  ~500 historical PRs with line items and approvals
  docs/index.json         registry of generated documents for ingestion
  docs/contracts/*.md     contract texts (contain seeded PII -> exercises masking)
  docs/poisoned/*.md      planted indirect-prompt-injection documents (ground truth for the scanner)

Re-running with the same SEED reproduces byte-identical output.

    uv run python -m synthetic.generate
"""

import json
import random
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from faker import Faker

from common.settings import get_settings
from synthetic import org
from synthetic.contracts import render_contract
from synthetic.poisoned import POISONED_DOCS
from synthetic.policy_specs import SPECS as POLICY_SPECS

SEED = 20261030
N_VENDORS = 200
N_CONTRACTS = 30
N_PURCHASE_REQUESTS = 500


IBAN_LOCALES = {"DE": "de_DE", "FR": "fr_FR", "NL": "nl_NL", "GB": "en_GB", "ES": "es_ES", "IT": "it_IT"}


def _vendors(fake: Faker, rng: random.Random) -> list[dict[str, Any]]:
    # One multi-locale Faker; Faker.seed() seeds the shared generator, so this stays deterministic.
    eu = Faker(list(IBAN_LOCALES.values()))
    vendors = []
    categories = list(org.VENDOR_CATEGORIES)
    for i in range(1, N_VENDORS + 1):
        country = rng.choice(list(IBAN_LOCALES))
        vendors.append(
            {
                "id": f"V-{i:04d}",
                "name": fake.unique.company(),
                "category": categories[(i - 1) % len(categories)],
                "status": rng.choices(["active", "onboarding", "suspended", "offboarded"], [80, 8, 7, 5])[0],
                "risk_rating": rng.choices(["low", "medium", "high"], [65, 28, 7])[0],
                "contact_name": fake.name(),
                "contact_email": fake.unique.company_email(),
                "phone": fake.phone_number(),
                # Faker IBANs carry valid mod-97 check digits, so checksum-validating detectors fire.
                "iban": eu[IBAN_LOCALES[country]].iban(),
                "tax_id": f"{rng.randint(10, 99)}-{rng.randint(1000000, 9999999)}",  # US EIN shape
            }
        )
    return vendors


def _contracts(vendors: list[dict[str, Any]], rng: random.Random) -> list[dict[str, Any]]:
    # Prefer active vendors in categories that carry real contracts.
    contractable = {
        "Software Licenses",
        "Cloud Services",
        "Facilities & Maintenance",
        "Logistics",
        "Marketing Services",
        "Legal Services",
        "Consulting",
        "Telecom",
        "IT Hardware",
    }
    pool = [v for v in vendors if v["status"] == "active" and v["category"] in contractable]
    chosen = sorted(rng.sample(pool, N_CONTRACTS), key=lambda v: v["id"])
    contracts = []
    for i, v in enumerate(chosen, start=1):
        dept = rng.choice(org.VENDOR_CATEGORIES[v["category"]])
        start = date(2024, 1, 1) + timedelta(days=rng.randint(0, 900))
        years = rng.choice([1, 2, 3])
        value = round(rng.choice([24, 48, 60, 90, 120, 180, 250, 400, 750]) * 1000 * years, 2)
        facts = {
            "term_years": years,
            "auto_renew": rng.random() < 0.6,
            "renewal_notice_days": rng.choice([30, 60, 90]),
            "payment_terms_days": rng.choice([30, 45, 60]),
            "uptime_sla_pct": rng.choice([99.0, 99.5, 99.9, 99.95]),
            "service_credit_pct": rng.choice([5, 10, 15]),
            "termination_notice_days": rng.choice([30, 60, 90]),
            "liability_cap_multiple": rng.choice([1, 2]),  # x annual fees
            "governing_law": rng.choice(["New York", "Delaware", "California", "England and Wales"]),
            "late_fee_pct_month": rng.choice([1.0, 1.5]),
        }
        # High-value contracts are confidential (finance/legal/admin); others are internal to the buyer dept.
        classification = "confidential" if value >= 500_000 else "internal"
        contracts.append(
            {
                "id": f"C-{i:04d}",
                "vendor_id": v["id"],
                # The id is in the title so it reaches every chunk's embedded header: users refer to
                # contracts by id ("terminate C-0007"), and clause chunks never repeat it (eval finding).
                "title": f"{v['category']} Agreement C-{i:04d} — {v['name']}",
                "department": dept,
                "classification": classification,
                "start_date": start.isoformat(),
                "end_date": (start + timedelta(days=365 * years - 1)).isoformat(),
                "value": value,
                "currency": "USD",
                "doc_id": f"acme-contract-c-{i:04d}",
                "facts": facts,
            }
        )
    return contracts


def _line_items(fake: Faker, rng: random.Random, category: str, target: float) -> list[dict[str, Any]]:
    """1–4 items whose total equals `target` to the cent (last item absorbs rounding)."""
    n = rng.randint(1, 4)
    weights = [rng.random() + 0.2 for _ in range(n)]
    shares = [target * w / sum(weights) for w in weights]
    items = []
    remaining = round(target, 2)
    for idx, share in enumerate(shares, start=1):
        qty = rng.choice([1, 1, 1, 2, 5, 10]) if idx < n else 1
        unit = round(share / qty, 2) if idx < n else round(remaining, 2)
        remaining = round(remaining - unit * qty, 2)
        items.append(
            {
                "line_no": idx,
                "sku": f"{category[:3].upper()}-{rng.randint(10000, 99999)}",
                "description": f"{category}: {fake.bs().capitalize()}",
                "qty": qty,
                "unit_price": unit,
            }
        )
    return items


def _purchase_requests(
    fake: Faker, rng: random.Random, vendors: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    users = org.PERSONAS
    managers = {p.dept: p for p in users if p.role == "manager"}
    finance = [p for p in users if p.role == "finance"]
    admins = [p for p in users if p.role == "admin"]
    by_cat: dict[str, list[dict[str, Any]]] = {}
    for v in vendors:
        if v["status"] in {"active", "offboarded"}:
            by_cat.setdefault(v["category"], []).append(v)

    prs = []
    counters = {2025: 0, 2026: 0}
    for _ in range(N_PURCHASE_REQUESTS):
        requester = rng.choice(users)
        dept_cats = [c for c, depts in org.VENDOR_CATEGORIES.items() if requester.dept in depts]
        category = rng.choice(dept_cats)
        vendor = rng.choice(by_cat[category])
        # Log-normal: median ~$2.7k with a long tail, capped at 8% of the department's annual budget
        # so small departments stay within budget; only IT/Operations/Marketing reach the $100k admin level.
        cap = 0.08 * org.BUDGETS[requester.dept]
        total = round(min(max(rng.lognormvariate(7.9, 1.5), 40.0), cap), 2)
        year = rng.choices([2025, 2026], [45, 55])[0]
        created = (
            date(year, 1, 1) + timedelta(days=rng.randint(0, 300 if year == 2026 else 364))
        ).isoformat()
        counters[year] += 1
        chain = org.approval_chain(total)

        status, decided_levels = _pick_outcome(rng, chain, year)
        approvals = []
        used = {requester.id}
        for level, decision in decided_levels:
            approver = _approver_for(level, requester, total, managers, finance, admins, used, rng)
            used.add(approver.id)
            approvals.append(
                {
                    "level": level,
                    "approver_id": approver.id,
                    "decision": decision,
                    "comment": ""
                    if decision == "approved"
                    else rng.choice(
                        [
                            "Over budget for this quarter.",
                            "Use preferred vendor instead.",
                            "Insufficient justification.",
                        ]
                    ),
                }
            )

        prs.append(
            {
                "id": f"PR-{year}-{counters[year]:05d}",
                "requester_id": requester.id,
                "department_id": requester.dept,
                "vendor_id": vendor["id"],
                "total": total,
                "currency": "USD",
                "justification": fake.sentence(nb_words=12),
                "status": status,
                "approval_chain": chain,
                "created_at": created,
                "line_items": _line_items(fake, rng, category, total),
                "approvals": approvals,
            }
        )
    return prs


def _pick_outcome(rng: random.Random, chain: list[str], year: int) -> tuple[str, list[tuple[str, str]]]:
    """Choose a final status and the (level, decision) records consistent with it."""
    if not chain:  # auto-approved
        status = rng.choices(["ordered", "approved", "cancelled"], [70, 25, 5])[0]
        return status, []
    roll = rng.random()
    if year == 2026 and roll < 0.25:  # still in flight
        stop = rng.randrange(len(chain))
        return f"pending_{chain[stop]}", [(lvl, "approved") for lvl in chain[:stop]]
    if roll < 0.37:
        stop = rng.randrange(len(chain))
        return "rejected", [(lvl, "approved") for lvl in chain[:stop]] + [(chain[stop], "rejected")]
    status = rng.choices(["ordered", "approved"], [70, 30])[0]
    return status, [(lvl, "approved") for lvl in chain]


def _approver_for(
    level: str,
    requester: org.Persona,
    total: float,
    managers: dict[str, org.Persona],
    finance: list[org.Persona],
    admins: list[org.Persona],
    used: set[str],
    rng: random.Random,
) -> org.Persona:
    """Mirror policies/authz.rego: same-dept manager, else finance covers the manager level."""
    if level == "manager":
        mgr = managers.get(requester.dept)
        is_final = total <= org.FINANCE_THRESHOLD
        if mgr and mgr.id not in used and not (is_final and total > mgr.approval_limit):
            return mgr
        candidates = finance
    elif level == "finance":
        candidates = finance
    else:
        candidates = admins
    free = [p for p in candidates if p.id not in used]
    if not free:
        raise RuntimeError(f"no eligible {level} approver for {requester.id}")
    return rng.choice(free)


def _budgets(prs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    committed = dict.fromkeys(org.BUDGETS, 0.0)
    spent = dict.fromkeys(org.BUDGETS, 0.0)
    for pr in prs:
        if not pr["id"].startswith(f"PR-{org.FISCAL_YEAR}-"):
            continue
        if pr["status"] == "approved":
            committed[pr["department_id"]] += pr["total"]
        elif pr["status"] == "ordered":
            spent[pr["department_id"]] += pr["total"]
    out = []
    for dept, allocated in org.BUDGETS.items():
        if committed[dept] + spent[dept] > allocated:
            raise RuntimeError(
                f"synthetic spend exceeds {dept} budget; adjust BUDGETS or the PR distribution"
            )
        out.append(
            {
                "department_id": dept,
                "fiscal_year": org.FISCAL_YEAR,
                "allocated": allocated,
                "committed": round(committed[dept], 2),
                "spent": round(spent[dept], 2),
            }
        )
    return out


def main() -> None:
    out = get_settings().data_dir / "synthetic"
    rng = random.Random(SEED)
    fake = Faker("en_US")
    Faker.seed(SEED)

    vendors = _vendors(fake, rng)
    contracts = _contracts(vendors, rng)
    prs = _purchase_requests(fake, rng, vendors)
    budgets = _budgets(prs)
    vendor_by_id = {v["id"]: v for v in vendors}

    docs_index: list[dict[str, Any]] = []
    for c in contracts:
        path = Path("docs/contracts") / f"{c['id']}.md"
        _write(out / path, render_contract(c, vendor_by_id[c["vendor_id"]]))
        docs_index.append(
            {
                "id": c["doc_id"],
                "path": str(path),
                "title": c["title"],
                "source": "acme",
                "department": c["department"],
                "classification": c["classification"],
                "effective_date": c["start_date"],
                "poisoned": False,
            }
        )
    # Policy documents are generated once by synthetic.policies (LLM) and frozen; register those present.
    for spec in POLICY_SPECS:
        path = Path("docs/policies") / f"{spec.id}.md"
        if (out / path).exists():
            docs_index.append(
                {
                    "id": spec.id,
                    "path": str(path),
                    "title": spec.title,
                    "source": "acme",
                    "department": spec.department,
                    "classification": spec.classification,
                    "effective_date": spec.effective_date,
                    "poisoned": False,
                }
            )
    for p in POISONED_DOCS:
        path = Path("docs/poisoned") / f"{p['id']}.md"
        _write(out / path, p["text"])
        docs_index.append(
            {
                "id": p["id"],
                "path": str(path),
                "title": p["title"],
                "source": "acme",
                "department": p["department"],
                "classification": p["classification"],
                "effective_date": "2026-03-01",
                "poisoned": True,
                "technique": p["technique"],
            }
        )

    org_json = {
        "departments": [{"id": k, "name": v} for k, v in org.DEPARTMENTS.items()],
        "users": [
            {
                "id": p.id,
                "email": p.email,
                "name": p.name,
                "role": p.role,
                "department_id": p.dept,
                "spend_limit": p.spend_limit,
                "approval_limit": p.approval_limit,
            }
            for p in org.PERSONAS
        ],
        "budgets": budgets,
    }
    _write_json(out / "org.json", org_json)
    _write_json(out / "vendors.json", vendors)
    _write_json(out / "contracts.json", contracts)
    _write_json(out / "purchase_requests.json", prs)
    _write_json(out / "docs" / "index.json", docs_index)
    print(
        f"wrote {len(vendors)} vendors, {len(contracts)} contracts, {len(prs)} PRs, "
        f"{len(POISONED_DOCS)} poisoned docs -> {out}"
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def _write_json(path: Path, obj: Any) -> None:
    _write(path, json.dumps(obj, indent=2, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
