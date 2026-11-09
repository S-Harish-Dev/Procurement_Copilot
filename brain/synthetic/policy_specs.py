"""Specs for Acme's ~40 internal policy documents (generated once via a free LLM API, then frozen).

`facts` are canonical statements the document MUST contain verbatim in substance; they are the
gold answers for eval questions and, for approvals, must agree with policies/data.json
(test_synthetic checks the thresholds). Everything else is left to the generator.
"""

import json
from dataclasses import dataclass, field

from common.settings import REPO_ROOT


@dataclass(frozen=True)
class PolicySpec:
    id: str
    title: str
    department: str  # owning department, or "all"
    classification: str  # public | internal | confidential | restricted
    effective_date: str
    facts: list[str] = field(default_factory=list)


def _approval_facts() -> list[str]:
    a = json.loads((REPO_ROOT / "policies" / "data.json").read_text())["acme"]["approval"]
    return [
        f"Purchase requests up to and including USD {a['auto_approve_max']:,} are approved automatically.",
        f"Requests above USD {a['auto_approve_max']:,} require approval by the requester's department manager.",
        f"Requests above USD {a['finance_threshold']:,} additionally require Finance approval.",
        f"Requests above USD {a['admin_threshold']:,} additionally require approval by an Administrator.",
        "No one may approve their own request, and one person may approve only one level of a request.",
        "If a manager's personal approval limit is lower than the amount and the manager is the final level, "
        "the request is routed to Finance.",
        "Departments without an available manager have their manager-level approvals handled by Finance.",
    ]


SPECS: list[PolicySpec] = [
    # --- company-wide (internal, visible to everyone) ---
    PolicySpec(
        "acme-pol-procurement",
        "Acme Procurement Policy",
        "all",
        "internal",
        "2026-01-01",
        [
            "All purchases of goods and services must be raised as a purchase request in the procurement system.",
            "At least three written quotes are required for purchases above USD 25,000.",
            "A competitive tender is required for purchases above USD 250,000.",
            "Purchases must use an approved (active) vendor; suspended vendors may not receive new orders.",
            "Splitting a purchase into smaller requests to avoid an approval threshold is prohibited.",
        ],
    ),
    PolicySpec(
        "acme-pol-approval-matrix",
        "Purchase Approval Matrix",
        "all",
        "internal",
        "2026-01-01",
        _approval_facts(),
    ),
    PolicySpec(
        "acme-pol-vendor-onboarding",
        "Vendor Onboarding Procedure",
        "all",
        "internal",
        "2026-02-01",
        [
            "New vendors must provide a W-9 or equivalent tax form, a bank confirmation letter and an insurance "
            "certificate.",
            "Onboarding takes 10 business days after all documents are received.",
            "Every new vendor undergoes a risk review; high-risk vendors require Legal sign-off.",
            "Bank detail changes require written notice and a telephone call-back to a known contact.",
        ],
    ),
    PolicySpec(
        "acme-pol-coi",
        "Conflict of Interest Policy",
        "all",
        "internal",
        "2025-07-01",
        [
            "Employees must declare any financial interest in a vendor before participating in its selection.",
            "Gifts from vendors above USD 100 in value must be declared and may not be accepted during an "
            "active tender.",
        ],
    ),
    PolicySpec(
        "acme-pol-travel",
        "Travel and Expense Policy",
        "all",
        "internal",
        "2026-01-01",
        [
            "Economy class is required for flights under 6 hours; premium economy is allowed for 6 hours or more.",
            "The hotel cap is USD 250 per night in tier-1 cities and USD 180 elsewhere.",
            "The meal per diem is USD 75 per day.",
            "Expense claims must be submitted within 30 days with itemised receipts.",
        ],
    ),
    PolicySpec(
        "acme-pol-it-hardware",
        "IT Hardware Standards",
        "all",
        "internal",
        "2026-03-01",
        [
            "Standard laptops are refreshed every 36 months; engineering workstations every 30 months.",
            "The standard laptop budget is USD 1,800; engineering workstations USD 3,200.",
            "All hardware must be purchased through IT; personal purchases are not reimbursed.",
        ],
    ),
    PolicySpec(
        "acme-pol-software",
        "Software Licensing and SaaS Policy",
        "all",
        "internal",
        "2026-03-01",
        [
            "All SaaS subscriptions require an IT security review before purchase.",
            "Software handling customer data must support SSO and provide a SOC 2 Type II report.",
        ],
    ),
    PolicySpec(
        "acme-pol-pcard",
        "Purchasing Card (P-Card) Policy",
        "all",
        "internal",
        "2025-10-01",
        [
            "P-Card single transactions are limited to USD 2,500 and USD 10,000 per month per cardholder.",
            "P-Cards may not be used for travel, gifts, or recurring subscriptions.",
        ],
    ),
    PolicySpec(
        "acme-pol-sustainable",
        "Sustainable Procurement Guidelines",
        "all",
        "public",
        "2025-09-01",
        [
            "Acme targets 40% of addressable spend with vendors holding a recognised sustainability certification "
            "by 2028.",
        ],
    ),
    PolicySpec(
        "acme-pol-supplier-code",
        "Supplier Code of Conduct",
        "all",
        "public",
        "2025-01-01",
        [
            "Suppliers must prohibit forced and child labour throughout their supply chain.",
            "Suppliers must report suspected bribery to Acme's ethics hotline within 5 business days.",
        ],
    ),
    PolicySpec(
        "acme-pol-contract-mgmt",
        "Contract Management Procedure",
        "all",
        "internal",
        "2026-01-15",
        [
            "Contract owners must review auto-renewing contracts at least 90 days before the renewal date.",
            "Contracts above USD 100,000 require Legal review before signature.",
        ],
    ),
    PolicySpec(
        "acme-pol-receiving",
        "Goods Receipt and Three-Way Match",
        "all",
        "internal",
        "2025-11-01",
        [
            "Invoices are paid only after a three-way match of purchase order, goods receipt and invoice.",
            "Price variances above 5% or USD 500 require buyer approval before payment.",
        ],
    ),
    PolicySpec(
        "acme-pol-emergency",
        "Emergency Purchasing Procedure",
        "all",
        "internal",
        "2025-12-01",
        [
            "Emergency purchases may be made before approval only to prevent safety risks or major outages.",
            "A retrospective purchase request must be raised within 2 business days of an emergency purchase.",
        ],
    ),
    PolicySpec(
        "acme-pol-records",
        "Procurement Records Retention",
        "all",
        "internal",
        "2025-06-01",
        [
            "Procurement records are retained for 7 years after contract end.",
        ],
    ),
    PolicySpec(
        "acme-pol-data-protection",
        "Vendor Data Protection Requirements",
        "all",
        "internal",
        "2026-02-15",
        [
            "Vendors processing personal data must sign the Data Processing Addendum before access is granted.",
            "Vendors must notify Acme of a personal data breach within 72 hours.",
        ],
    ),
    PolicySpec("acme-pol-faq", "Procurement FAQ for Employees", "all", "public", "2026-04-01", []),
    # --- department-internal ---
    PolicySpec(
        "acme-it-cloud",
        "IT Cloud Spend Guidelines",
        "IT",
        "internal",
        "2026-02-01",
        [
            "Reserved instances or savings plans are required for workloads running more than 6 months.",
            "Cloud budgets alert at 80% and freeze non-production spend at 100% of the monthly allocation.",
        ],
    ),
    PolicySpec(
        "acme-it-security-review", "IT Vendor Security Review Checklist", "IT", "internal", "2026-01-10", []
    ),
    PolicySpec(
        "acme-it-asset",
        "IT Asset Disposal Procedure",
        "IT",
        "internal",
        "2025-08-01",
        [
            "Retired devices must be wiped to NIST 800-88 and disposed of through a certified recycler.",
        ],
    ),
    PolicySpec(
        "acme-hr-training",
        "Training and Development Purchasing",
        "HR",
        "internal",
        "2026-01-01",
        [
            "Each employee has an annual training allowance of USD 1,500.",
            "External training above USD 3,000 requires a 12-month retention agreement.",
        ],
    ),
    PolicySpec(
        "acme-hr-recruiting",
        "Recruitment Agency Engagement",
        "HR",
        "internal",
        "2025-09-15",
        [
            "Agency fees are capped at 18% of first-year base salary.",
        ],
    ),
    PolicySpec("acme-hr-benefits", "Benefits Vendor Management", "HR", "internal", "2025-11-15", []),
    PolicySpec(
        "acme-mkt-agency",
        "Marketing Agency Engagement Rules",
        "Marketing",
        "internal",
        "2026-01-20",
        [
            "Agency retainers are reviewed quarterly against agreed KPIs.",
            "Creative work is owned by Acme on payment; agencies must assign IP in the contract.",
        ],
    ),
    PolicySpec(
        "acme-mkt-events",
        "Events and Sponsorship Purchasing",
        "Marketing",
        "internal",
        "2025-10-15",
        [
            "Event sponsorships above USD 50,000 require a written ROI plan.",
        ],
    ),
    PolicySpec(
        "acme-ops-facilities",
        "Facilities Services Procurement",
        "Operations",
        "internal",
        "2025-12-15",
        [
            "Facilities contracts include a 30-day termination for convenience clause.",
        ],
    ),
    PolicySpec(
        "acme-ops-logistics",
        "Logistics and Freight Procurement",
        "Operations",
        "internal",
        "2026-02-20",
        [
            "Freight contracts must include fuel surcharge bands tied to a published diesel index.",
        ],
    ),
    PolicySpec(
        "acme-ops-inventory", "Inventory and Stock Purchasing", "Operations", "internal", "2025-07-15", []
    ),
    PolicySpec(
        "acme-legal-outside-counsel",
        "Outside Counsel Guidelines",
        "Legal",
        "internal",
        "2026-01-05",
        [
            "Outside counsel must provide a budget estimate before work begins and bill in 6-minute increments.",
        ],
    ),
    PolicySpec(
        "acme-legal-templates", "Standard Contract Templates Guide", "Legal", "internal", "2025-11-20", []
    ),
    PolicySpec(
        "acme-fin-close",
        "Month-End Close: Accruals for Purchases",
        "Finance",
        "internal",
        "2025-10-20",
        [
            "Goods received but not invoiced by the 3rd business day are accrued at the PO price.",
        ],
    ),
    PolicySpec(
        "acme-fin-payments",
        "Payment Run Procedure",
        "Finance",
        "internal",
        "2026-01-12",
        [
            "Payment runs occur every Tuesday and Thursday.",
        ],
    ),
    # --- confidential (finance role, Finance/Legal departments, admins) ---
    PolicySpec(
        "acme-conf-budget-fy26",
        "FY2026 Budget Allocation Memo",
        "Finance",
        "confidential",
        "2025-12-20",
        [
            "FY2026 budgets: IT USD 2,500,000; Operations USD 1,800,000; Marketing USD 1,200,000; Legal USD 600,000; "
            "HR USD 400,000; Finance USD 300,000.",
            "A mid-year review in July may reallocate up to 10% of any department's unspent budget.",
        ],
    ),
    PolicySpec(
        "acme-conf-vendor-risk", "Vendor Risk Register Summary", "Finance", "confidential", "2026-03-15", []
    ),
    PolicySpec(
        "acme-conf-savings",
        "Procurement Savings Targets FY2026",
        "Finance",
        "confidential",
        "2026-01-08",
        [
            "The FY2026 procurement savings target is 6% of addressable spend.",
        ],
    ),
    PolicySpec(
        "acme-conf-litigation",
        "Vendor Dispute and Litigation Guidance",
        "Legal",
        "confidential",
        "2025-09-30",
        [],
    ),
    PolicySpec(
        "acme-conf-negotiation",
        "Negotiation Playbook for Strategic Vendors",
        "Finance",
        "confidential",
        "2026-02-05",
        [
            "Target payment terms are Net 60 for strategic vendors; accept Net 45 only for a price concession "
            "of at least 2%.",
        ],
    ),
    # --- restricted (admins only) ---
    PolicySpec(
        "acme-res-fraud",
        "Procurement Fraud Investigation Procedure",
        "all",
        "restricted",
        "2025-08-20",
        [
            "Suspected procurement fraud is reported to the Chief Compliance Officer within 24 hours.",
        ],
    ),
    PolicySpec(
        "acme-res-access",
        "Procurement System Access Control Standard",
        "all",
        "restricted",
        "2026-01-25",
        [
            "Administrator access to the procurement system is reviewed quarterly.",
        ],
    ),
    PolicySpec(
        "acme-res-ma",
        "M&A Vendor Consolidation Plan (Project Larch)",
        "all",
        "restricted",
        "2026-04-10",
        [
            "Project Larch consolidates logistics vendors from 14 to 5 by end of FY2027.",
        ],
    ),
]
