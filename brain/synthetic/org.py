"""Fixed organisational facts for Acme Corp. Hand-written (not random) so demo personas stay stable."""

from dataclasses import dataclass

DEPARTMENTS: dict[str, str] = {
    "IT": "Information Technology",
    "HR": "Human Resources",
    "Finance": "Finance",
    "Legal": "Legal",
    "Operations": "Operations",
    "Marketing": "Marketing",
}

FISCAL_YEAR = 2026

# FY budget allocations (USD).
BUDGETS: dict[str, int] = {
    "IT": 2_500_000,
    "HR": 400_000,
    "Finance": 300_000,
    "Legal": 600_000,
    "Operations": 1_800_000,
    "Marketing": 1_200_000,
}


@dataclass(frozen=True)
class Persona:
    id: str
    name: str
    role: str
    dept: str
    spend_limit: int
    approval_limit: int

    @property
    def email(self) -> str:
        # .example is reserved (RFC 2606) — these addresses can never reach a real inbox.
        return f"{self.id}@acme.example"


# 2–3 per role across departments. grace.kim has a low personal approval limit so the
# "exceeds your approval limit; routed to Finance" escalation is demo-able.
PERSONAS: list[Persona] = [
    Persona("priya.sharma", "Priya Sharma", "employee", "IT", 5_000, 0),
    Persona("marcus.lee", "Marcus Lee", "employee", "Marketing", 5_000, 0),
    Persona("sofia.alvarez", "Sofia Alvarez", "employee", "Operations", 5_000, 0),
    Persona("daniel.okafor", "Daniel Okafor", "employee", "Legal", 5_000, 0),
    Persona("noah.williams", "Noah Williams", "employee", "HR", 5_000, 0),
    Persona("james.chen", "James Chen", "manager", "IT", 25_000, 25_000),
    Persona("aisha.rahman", "Aisha Rahman", "manager", "Marketing", 25_000, 25_000),
    Persona("tom.becker", "Tom Becker", "manager", "Operations", 25_000, 25_000),
    Persona("grace.kim", "Grace Kim", "manager", "HR", 10_000, 5_000),
    # Every department needs its own manager: without one, a finance user's >$10k Finance-dept
    # request has nobody left to sign the finance level (found by the generator).
    Persona("lucas.martin", "Lucas Martin", "manager", "Finance", 25_000, 25_000),
    Persona("hannah.schmidt", "Hannah Schmidt", "manager", "Legal", 25_000, 25_000),
    Persona("elena.petrova", "Elena Petrova", "finance", "Finance", 100_000, 100_000),
    Persona("ravi.iyer", "Ravi Iyer", "finance", "Finance", 100_000, 100_000),
    Persona("olivia.grant", "Olivia Grant", "admin", "Operations", 1_000_000, 10_000_000),
    # Second admin so an admin's own >$100k request can be approved by someone else.
    Persona("kenji.watanabe", "Kenji Watanabe", "admin", "IT", 1_000_000, 10_000_000),
]

# Must match policies/data.json (acme.approval). A unit test asserts they agree.
AUTO_APPROVE_MAX = 1_000
FINANCE_THRESHOLD = 10_000
ADMIN_THRESHOLD = 100_000


def approval_chain(amount: float) -> list[str]:
    chain = []
    if amount > AUTO_APPROVE_MAX:
        chain.append("manager")
    if amount > FINANCE_THRESHOLD:
        chain.append("finance")
    if amount > ADMIN_THRESHOLD:
        chain.append("admin")
    return chain


VENDOR_CATEGORIES: dict[str, list[str]] = {
    # category -> departments that typically buy from it
    "IT Hardware": ["IT"],
    "Software Licenses": ["IT", "Finance", "Legal"],
    "Cloud Services": ["IT"],
    "Office Supplies": ["Operations", "HR", "Finance", "Legal", "Marketing", "IT"],
    "Facilities & Maintenance": ["Operations"],
    "Logistics": ["Operations"],
    "Marketing Services": ["Marketing"],
    "Legal Services": ["Legal"],
    "Consulting": ["IT", "Finance", "Operations", "HR"],
    "Travel": ["Marketing", "Operations", "IT", "HR"],
    "Telecom": ["IT", "Operations"],
    "Training": ["HR", "IT"],
}
