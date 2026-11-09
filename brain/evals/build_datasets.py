"""Build evals/datasets/qa.jsonl deterministically from ground truth (no LLM involved).

Every answerable case gets gold chunk ids by locating the answer evidence in the index bundle
(original chunk text), so gold labels come from data whose answers are known:
- contract_fact   : the 30 Acme contracts' `facts` (each number is in the contract text)
- policy_fact     : the policy specs' required facts (checked present in the generated docs)
- cuad_clause     : CUAD's expert clause annotations (span text located in our chunks)
- id_lookup       : FAR sections by number only (exact-ID queries -> the reason BM25 exists)
- role_restricted : confidential/restricted questions asked by an employee -> expected no-answer
- unanswerable    : plausible procurement questions the corpus does not answer
- paraphrase      : pairs of rewordings (semantic cache tests, M7)
The builder fails if any gold evidence can't be found: a silently empty gold set would inflate
recall-based metrics with unanswerable "answerable" cases.

    uv run python -m evals.build_datasets
"""

import hashlib
import json
import random
import re
import zipfile
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from common.settings import REPO_ROOT, get_settings

OUT = REPO_ROOT / "evals" / "datasets" / "qa.jsonl"
SEED = 20261031

# One persona per department who sees that department's internal docs (Finance has no employee).
DEPT_PERSONA = {
    "IT": "priya.sharma",
    "Marketing": "marcus.lee",
    "Operations": "sofia.alvarez",
    "Legal": "daniel.okafor",
    "HR": "noah.williams",
    "Finance": "lucas.martin",
    "all": "priya.sharma",
}
FINANCE = "elena.petrova"
ADMIN = "olivia.grant"
OUTSIDER = "marcus.lee"  # Marketing employee: no confidential/restricted access, no IT/Ops internals


def _load_chunks() -> dict[str, list[dict[str, Any]]]:
    by_doc: dict[str, list[dict[str, Any]]] = defaultdict(list)
    with (get_settings().data_dir / "index_bundle" / "chunks.jsonl").open() as f:
        for line in f:
            c = json.loads(line)
            if not c["quarantined"]:
                by_doc[c["doc_id"]].append(c)
    return by_doc


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip().lower()


def _gold(
    chunks: list[dict[str, Any]], *, section: str | None = None, contains: str | None = None
) -> list[str]:
    ids = []
    for c in chunks:
        if section is not None and not c["section_path"].endswith(section):
            continue
        if contains is not None and _norm(contains) not in _norm(c["text_original"]):
            continue
        ids.append(c["chunk_id"])
    return ids


def _case(
    cid: str,
    q: str,
    persona: str,
    gold: list[str],
    answer: str,
    category: str,
    answerable: bool = True,
    forbidden: list[str] | None = None,
) -> dict[str, Any]:
    if answerable and not gold:
        raise RuntimeError(f"no gold chunk for {cid}: {q}")
    case = {
        "id": cid,
        "question": q,
        "persona": persona,
        "gold_chunk_ids": gold,
        "gold_answer": answer,
        "answerable": answerable,
        "category": category,
    }
    if forbidden:
        # Hard gate: no chunk of these documents may ever be retrieved for this persona.
        case["forbidden_doc_ids"] = forbidden
    return case


def _other_dept_persona(dept: str, k: int) -> str:
    """An employee of a *different* department (rotates for variety)."""
    others = [p for d, p in DEPT_PERSONA.items() if d not in (dept, "all", "Finance")]
    return others[k % len(others)]


# -------------------------------------------------------------------------------------------------
def contract_cases(by_doc: dict[str, list[dict[str, Any]]], rng: random.Random) -> list[dict[str, Any]]:
    contracts = json.loads((get_settings().data_dir / "synthetic" / "contracts.json").read_text())
    vendors = {
        v["id"]: v for v in json.loads((get_settings().data_dir / "synthetic" / "vendors.json").read_text())
    }
    templates: list[tuple[str, str, Callable[[dict[str, Any]], str], str | None]] = [
        (
            "2. Term and Renewal",
            "Does contract {id} with {vendor} renew automatically?",
            lambda f: (
                f"Yes, unless notice is given {f['renewal_notice_days']} days before the term ends"
                if f["auto_renew"]
                else "No; extension needs a signed written amendment"
            ),
            None,
        ),
        (
            "4. Fees and Payment",
            "What are the payment terms in our contract with {vendor} ({id})?",
            lambda f: f"Net {f['payment_terms_days']}",
            "Net {payment_terms_days}",
        ),
        (
            "5. Service Levels",
            "What availability does {vendor} guarantee under contract {id}?",
            lambda f: f"{f['uptime_sla_pct']}% monthly availability",
            "{uptime_sla_pct}%",
        ),
        (
            "6. Termination",
            "How much notice is needed to terminate contract {id} for convenience?",
            lambda f: f"{f['termination_notice_days']} days' written notice",
            "{termination_notice_days} days",
        ),
        (
            "10. Governing Law",
            "Which law governs the {vendor} agreement ({id})?",
            lambda f: f["governing_law"],
            "{governing_law}",
        ),
        (
            "7. Limitation of Liability",
            "What is the liability cap in contract {id} with {vendor}?",
            lambda f: f"{f['liability_cap_multiple']}x the fees paid or payable in the prior 12 months",
            "{liability_cap_multiple}x the fees",
        ),
    ]
    cases = []
    for i, c in enumerate(contracts):
        vendor = vendors[c["vendor_id"]]["name"]
        chunks = by_doc[c["doc_id"]]
        persona = FINANCE if c["classification"] == "confidential" else DEPT_PERSONA[c["department"]]
        for j in (i % len(templates), (i + 3) % len(templates)):  # two different fields per contract
            section, qt, ans, anchor = templates[j]
            f = c["facts"]
            gold = _gold(chunks, section=section, contains=anchor.format(**f) if anchor else None)
            q = qt.format(id=c["id"], vendor=vendor)
            cases.append(_case(f"contract-{c['id']}-{j}", q, persona, gold, ans(f), "contract_fact"))
        section, qt, ans, _ = templates[i % len(templates)]
        q = qt.format(id=c["id"], vendor=vendor)
        if c["classification"] == "confidential":
            cases.append(
                _case(
                    f"restricted-{c['id']}",
                    q,
                    OUTSIDER,
                    [],
                    "NO_ANSWER",
                    "role_restricted",
                    answerable=False,
                    forbidden=[c["doc_id"]],
                )
            )
        elif c["department"] not in ("Finance", "Legal"):
            # Internal contract of another department (Finance/Legal see confidential docs, skip them).
            cases.append(
                _case(
                    f"crossdept-{c['id']}",
                    q,
                    _other_dept_persona(c["department"], i),
                    [],
                    "NO_ANSWER",
                    "role_restricted",
                    answerable=False,
                    forbidden=[c["doc_id"]],
                )
            )
    return cases


# (doc id, persona, question, evidence substring, gold answer)
POLICY_QUESTIONS: list[tuple[str, str, str, str, str]] = [
    (
        "acme-pol-approval-matrix",
        "priya.sharma",
        "Who has to approve a purchase request of USD 15,000?",
        "additionally require Finance approval",
        "Department manager, then Finance",
    ),
    (
        "acme-pol-approval-matrix",
        "priya.sharma",
        "Are purchase requests of 800 dollars approved automatically?",
        "approved automatically",
        "Yes, up to and including USD 1,000",
    ),
    (
        "acme-pol-approval-matrix",
        "noah.williams",
        "When does a purchase request need an Administrator's approval?",
        "100,000",
        "Above USD 100,000",
    ),
    (
        "acme-pol-approval-matrix",
        "sofia.alvarez",
        "Can I approve my own purchase request?",
        "No one may approve their own request",
        "No",
    ),
    (
        "acme-pol-approval-matrix",
        "marcus.lee",
        "What happens if my manager's approval limit is lower than the amount?",
        "routed to Finance",
        "It is routed to Finance",
    ),
    (
        "acme-pol-procurement",
        "priya.sharma",
        "How many written quotes do I need for a purchase above USD 25,000?",
        "three written quotes",
        "At least three",
    ),
    (
        "acme-pol-procurement",
        "sofia.alvarez",
        "When is a competitive tender required?",
        "250,000",
        "Above USD 250,000",
    ),
    (
        "acme-pol-procurement",
        "marcus.lee",
        "Can I split a purchase into smaller requests to avoid approval?",
        "prohibited",
        "No, it is prohibited",
    ),
    (
        "acme-pol-procurement",
        "noah.williams",
        "Can we place new orders with a suspended vendor?",
        "suspended vendors may not",
        "No",
    ),
    (
        "acme-pol-vendor-onboarding",
        "priya.sharma",
        "Which documents does a new vendor need to provide?",
        "W-9",
        "W-9 or equivalent tax form, bank confirmation letter, insurance certificate",
    ),
    (
        "acme-pol-vendor-onboarding",
        "sofia.alvarez",
        "How long does vendor onboarding take?",
        "10 business days",
        "10 business days after all documents are received",
    ),
    (
        "acme-pol-vendor-onboarding",
        "daniel.okafor",
        "Who must sign off on high-risk vendors?",
        "Legal sign-off",
        "Legal",
    ),
    (
        "acme-pol-coi",
        "marcus.lee",
        "What is the limit for accepting gifts from vendors?",
        "USD 100",
        "Gifts above USD 100 must be declared",
    ),
    (
        "acme-pol-travel",
        "priya.sharma",
        "What is the hotel cap per night in tier-1 cities?",
        "USD 250",
        "USD 250 per night",
    ),
    ("acme-pol-travel", "marcus.lee", "What is the daily meal per diem?", "USD 75", "USD 75 per day"),
    (
        "acme-pol-travel",
        "sofia.alvarez",
        "Can I fly premium economy on a 7-hour flight?",
        "premium economy",
        "Yes, for flights of 6 hours or more",
    ),
    (
        "acme-pol-travel",
        "noah.williams",
        "How soon must I submit expense claims?",
        "within 30 days",
        "Within 30 days with itemised receipts",
    ),
    (
        "acme-pol-it-hardware",
        "priya.sharma",
        "How often are standard laptops refreshed?",
        "36 months",
        "Every 36 months",
    ),
    (
        "acme-pol-it-hardware",
        "marcus.lee",
        "What is the budget for a standard laptop?",
        "USD 1,800",
        "USD 1,800",
    ),
    (
        "acme-pol-software",
        "priya.sharma",
        "What review does a new SaaS subscription need before purchase?",
        "security review",
        "An IT security review",
    ),
    (
        "acme-pol-pcard",
        "sofia.alvarez",
        "What is the single-transaction limit on a P-Card?",
        "USD 2,500",
        "USD 2,500 per transaction, USD 10,000 per month",
    ),
    (
        "acme-pol-contract-mgmt",
        "daniel.okafor",
        "When must auto-renewing contracts be reviewed?",
        "90 days before",
        "At least 90 days before the renewal date",
    ),
    (
        "acme-pol-contract-mgmt",
        "priya.sharma",
        "Which contracts require Legal review before signature?",
        "USD 100,000",
        "Contracts above USD 100,000",
    ),
    (
        "acme-pol-receiving",
        "noah.williams",
        "What is a three-way match?",
        "three-way match",
        "PO, goods receipt and invoice must match before payment",
    ),
    (
        "acme-pol-emergency",
        "sofia.alvarez",
        "How quickly must I raise a request after an emergency purchase?",
        "2 business days",
        "Within 2 business days",
    ),
    (
        "acme-pol-records",
        "daniel.okafor",
        "How long are procurement records retained?",
        "7 years",
        "7 years after contract end",
    ),
    (
        "acme-pol-data-protection",
        "priya.sharma",
        "How quickly must vendors report a personal data breach?",
        "72 hours",
        "Within 72 hours",
    ),
    (
        "acme-it-cloud",
        "priya.sharma",
        "When are reserved instances required for cloud workloads?",
        "6 months",
        "Workloads running more than 6 months",
    ),
    (
        "acme-hr-training",
        "noah.williams",
        "What is the annual training allowance per employee?",
        "USD 1,500",
        "USD 1,500",
    ),
    (
        "acme-hr-recruiting",
        "noah.williams",
        "What is the cap on recruitment agency fees?",
        "18%",
        "18% of first-year base salary",
    ),
    (
        "acme-mkt-events",
        "marcus.lee",
        "When does an event sponsorship need an ROI plan?",
        "USD 50,000",
        "Above USD 50,000",
    ),
    (
        "acme-ops-facilities",
        "sofia.alvarez",
        "What termination clause must facilities contracts include?",
        "30-day termination",
        "A 30-day termination for convenience clause",
    ),
    (
        "acme-legal-outside-counsel",
        "daniel.okafor",
        "In what increments must outside counsel bill?",
        "6-minute increments",
        "6-minute increments",
    ),
    (
        "acme-fin-payments",
        "lucas.martin",
        "On which days do payment runs occur?",
        "Tuesday and Thursday",
        "Tuesday and Thursday",
    ),
    (
        "acme-conf-budget-fy26",
        FINANCE,
        "What is the FY2026 budget for the IT department?",
        "USD 2,500,000",
        "USD 2,500,000",
    ),
    (
        "acme-conf-savings",
        FINANCE,
        "What is the FY2026 procurement savings target?",
        "6% of addressable spend",
        "6% of addressable spend",
    ),
    (
        "acme-conf-negotiation",
        FINANCE,
        "What payment terms do we target with strategic vendors?",
        "Net 60",
        "Net 60; Net 45 only for at least a 2% price concession",
    ),
    (
        "acme-res-fraud",
        ADMIN,
        "How quickly must suspected procurement fraud be reported?",
        "24 hours",
        "To the Chief Compliance Officer within 24 hours",
    ),
    (
        "acme-res-ma",
        ADMIN,
        "How many logistics vendors will Project Larch consolidate to?",
        "from 14 to 5",
        "From 14 to 5 by end of FY2027",
    ),
    (
        "acme-res-access",
        ADMIN,
        "How often is administrator access to the procurement system reviewed?",
        "quarterly",
        "Quarterly",
    ),
]


def policy_cases(by_doc: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    index = {
        e["id"]: e
        for e in json.loads((get_settings().data_dir / "synthetic" / "docs" / "index.json").read_text())
    }
    cases = []
    for n, (doc, persona, q, evidence, answer) in enumerate(POLICY_QUESTIONS):
        gold = _gold(by_doc[doc], contains=evidence)
        cases.append(_case(f"policy-{n:02d}", q, persona, gold, answer, "policy_fact"))
        entry = index[doc]
        if entry["classification"] in {"confidential", "restricted"}:
            cases.append(
                _case(
                    f"restricted-policy-{n:02d}",
                    q,
                    OUTSIDER,
                    [],
                    "NO_ANSWER",
                    "role_restricted",
                    answerable=False,
                    forbidden=[doc],
                )
            )
        elif entry["department"] not in ("all", "Finance", "Legal"):
            cases.append(
                _case(
                    f"crossdept-policy-{n:02d}",
                    q,
                    _other_dept_persona(entry["department"], n),
                    [],
                    "NO_ANSWER",
                    "role_restricted",
                    answerable=False,
                    forbidden=[doc],
                )
            )
    return cases


CUAD_CATEGORIES = {
    "Governing Law": "Which jurisdiction's law governs the {title}?",
    "Renewal Term": "What is the renewal term of the {title}?",
    "Notice Period To Terminate Renewal": "How much notice is needed to stop the {title} from renewing?",
    "Expiration Date": "When does the {title} expire?",
    "Cap On Liability": "Is liability capped under the {title}, and how?",
    "Warranty Duration": "How long is the warranty period in the {title}?",
}


def _cuad_title(raw: str) -> str:
    """'PHOTRONICSINC_..._EX-10.28-OUTSOURCING AGREEMENT' -> 'Photronicsinc outsourcing agreement'."""
    company = raw.split("_")[0].title()
    m = re.search(r"([A-Za-z][A-Za-z ,&/'()]*agreement[A-Za-z ]*)\s*\d*$", raw, re.IGNORECASE)
    kind = m.group(1).strip().lower() if m else "agreement"
    return f"{company} {kind}"


def cuad_cases(by_doc: dict[str, list[dict[str, Any]]], rng: random.Random) -> list[dict[str, Any]]:
    data = json.loads(
        zipfile.ZipFile(get_settings().data_dir / "raw" / "cuad" / "data.zip").read("CUADv1.json")
    )["data"]
    ours = {
        c["doc_id"]: c for chunks in by_doc.values() for c in chunks[:1] if c["doc_id"].startswith("cuad-")
    }
    title_to_doc = {}
    for doc_id in ours:
        title_to_doc[doc_id] = doc_id
    cases = []
    for doc in data:
        title = doc["title"]
        doc_id = next(
            (
                d
                for d in ours
                if d.endswith(hashlib.sha256(title.encode()).hexdigest()[:6]) and d.startswith("cuad-")
            ),
            None,
        )
        if doc_id is None:
            continue
        qas = {qa["id"].split("__")[-1]: qa for qa in doc["paragraphs"][0]["qas"]}
        for cat, template in CUAD_CATEGORIES.items():
            qa = qas.get(cat)
            if not qa or not qa["answers"]:
                continue
            span = qa["answers"][0]["text"]
            evidence = span[:60]
            gold = _gold(by_doc[doc_id], contains=evidence)
            if not gold:
                continue  # span crossed a chunk boundary or was masked; skip rather than mislabel
            cases.append(
                _case(
                    f"cuad-{doc_id[-6:]}-{cat.lower().replace(' ', '_')[:12]}",
                    template.format(title=_cuad_title(title)),
                    "priya.sharma",
                    gold,
                    span[:300],
                    "cuad_clause",
                )
            )
            break  # one question per contract keeps categories balanced
    rng.shuffle(cases)
    return cases[:45]


def far_cases(by_doc: dict[str, list[dict[str, Any]]], rng: random.Random) -> list[dict[str, Any]]:
    heading = re.compile(r"(?:^| › )(\d{1,2}\.\d{3}(?:-\d+)?) ([A-Z][^›]{3,80})$")
    sections: dict[str, tuple[str, list[str]]] = {}
    for c in by_doc["far"]:
        m = heading.search(c["section_path"])
        if m:
            num, title = m.group(1), m.group(2).strip().rstrip(".")
            sections.setdefault(num, (title, []))[1].append(c["chunk_id"])
    nums = sorted(sections)
    rng.shuffle(nums)
    cases = []
    for k, num in enumerate(nums[:30]):
        title, gold = sections[num]
        q = f"What does FAR {num} say?" if k % 2 == 0 else f"What does FAR {num} ({title}) cover?"
        cases.append(_case(f"far-{num}", q, "priya.sharma", gold, title, "id_lookup"))
    return cases


UNANSWERABLE = [
    "How many vacation days do Acme employees get per year?",
    "What is Acme's parental leave policy?",
    "Can vendors be paid in cryptocurrency?",
    "What is the cafeteria menu this week?",
    "What is our policy on buying company cars for executives?",
    "Who won the last company football tournament?",
    "What is the procurement policy for the Singapore subsidiary?",
    "How do I book a meeting room?",
    "What is the stock price target for Acme next year?",
    "Which vendors are owned by the CEO's family?",
    "What is the approval limit for purchases in Japanese yen?",
    "What is the policy for buying artwork for offices?",
    "How do I reset my laptop password?",
    "What is Acme's dress code?",
    "Does Acme sponsor employee gym memberships?",
    "What is the per diem for travel to Antarctica?",
    "What is the procurement policy for buying real estate?",
    "How much did Acme spend on catering in 2019?",
    "Which Acme vendor has the best coffee?",
    "What is the maximum bonus for procurement staff?",
    "What is the policy for purchasing drones?",
    "Can I expense a home espresso machine?",
    "What is Acme's carbon emissions total for 2025?",
    "How many employees does Acme have in Brazil?",
    "What discount does Acme get from airlines?",
    "Who is Acme's auditor?",
    "What is the procurement policy for buying livestock?",
    "What is the warranty on office chairs from Ikea?",
    "How do I file a patent at Acme?",
    "What is the policy on purchasing NFTs?",
    "What is the approval process for hiring interns?",
    "When is the next Acme all-hands meeting?",
    "What is the vendor code for the cleaning company in Paris?",
    "How do I order business cards?",
    "What is the procurement policy for space launch services?",
    "Which countries are on Acme's travel ban list?",
    "What is the exchange rate Acme uses for EUR invoices this month?",
    "Who approved the 2020 office renovation?",
    "What is Acme's policy on bring-your-own-device reimbursement?",
    "How many parking spaces does Acme lease?",
]

PARAPHRASES = [
    (
        "What is the hotel cap per night in tier-1 cities?",
        "How much can I spend on a hotel per night in a tier-1 city?",
    ),
    ("How often are standard laptops refreshed?", "What is the replacement cycle for a standard laptop?"),
    (
        "How many written quotes do I need for a purchase above USD 25,000?",
        "Above 25k, how many quotes are required?",
    ),
    ("What is the daily meal per diem?", "How much is the per diem for meals each day?"),
    ("How long does vendor onboarding take?", "What is the typical duration of onboarding a new supplier?"),
    (
        "Who has to approve a purchase request of USD 15,000?",
        "Which approvals does a 15,000 dollar purchase need?",
    ),
    (
        "What is the single-transaction limit on a P-Card?",
        "How much can I charge to a purchasing card in one transaction?",
    ),
    ("How long are procurement records retained?", "For how many years do we keep procurement records?"),
    ("When is a competitive tender required?", "At what amount must we run a competitive tender?"),
    (
        "How quickly must vendors report a personal data breach?",
        "Within what time must a supplier notify us of a data breach?",
    ),
]


def main() -> None:
    rng = random.Random(SEED)
    by_doc = _load_chunks()
    cases = (
        contract_cases(by_doc, rng) + policy_cases(by_doc) + cuad_cases(by_doc, rng) + far_cases(by_doc, rng)
    )
    cases += [
        _case(f"unanswerable-{i:02d}", q, "priya.sharma", [], "NO_ANSWER", "unanswerable", answerable=False)
        for i, q in enumerate(UNANSWERABLE)
    ]
    policy_gold = {q: c for c in cases if c["category"] == "policy_fact" for q in [c["question"]]}
    for i, (a, b) in enumerate(PARAPHRASES):
        base = policy_gold[a]
        cases.append(
            {
                **base,
                "id": f"paraphrase-{i:02d}",
                "question": b,
                "category": "paraphrase",
                "paraphrase_of": base["id"],
            }
        )
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in cases))
    counts: dict[str, int] = defaultdict(int)
    for c in cases:
        counts[c["category"]] += 1
    print(f"wrote {len(cases)} cases to {OUT}: {dict(counts)}")


if __name__ == "__main__":
    main()
