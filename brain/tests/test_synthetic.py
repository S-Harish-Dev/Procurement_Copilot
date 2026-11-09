import json
from typing import Any

import pytest

from common.settings import REPO_ROOT, get_settings
from synthetic import org


def _load(name: str) -> Any:
    return json.loads((get_settings().data_dir / "synthetic" / name).read_text())


def test_thresholds_match_opa_policy_data() -> None:
    approval = json.loads((REPO_ROOT / "policies" / "data.json").read_text())["acme"]["approval"]
    assert approval == {
        "auto_approve_max": org.AUTO_APPROVE_MAX,
        "finance_threshold": org.FINANCE_THRESHOLD,
        "admin_threshold": org.ADMIN_THRESHOLD,
    }


@pytest.mark.parametrize(
    ("amount", "chain"),
    [
        (1000, []),
        (1000.01, ["manager"]),
        (10000, ["manager"]),
        (10000.01, ["manager", "finance"]),
        (100000, ["manager", "finance"]),
        (100000.01, ["manager", "finance", "admin"]),
    ],
)
def test_approval_chain_boundaries(amount: float, chain: list[str]) -> None:
    assert org.approval_chain(amount) == chain


def test_every_department_has_a_manager() -> None:
    assert {p.dept for p in org.PERSONAS if p.role == "manager"} == set(org.DEPARTMENTS)


def test_historical_prs_respect_policy_invariants() -> None:
    users = {u["id"]: u for u in _load("org.json")["users"]}
    for pr in _load("purchase_requests.json"):
        assert pr["approval_chain"] == org.approval_chain(pr["total"]), pr["id"]
        approvers = [a["approver_id"] for a in pr["approvals"]]
        assert pr["requester_id"] not in approvers, f"self-approval in {pr['id']}"
        assert len(set(approvers)) == len(approvers), f"one person signed two levels of {pr['id']}"
        for a in pr["approvals"]:
            role = users[a["approver_id"]]["role"]
            assert role == a["level"] or (role == "finance" and a["level"] == "manager"), pr["id"]
            if role == "manager":
                assert users[a["approver_id"]]["department_id"] == pr["department_id"], pr["id"]
        assert abs(sum(li["qty"] * li["unit_price"] for li in pr["line_items"]) - pr["total"]) < 0.01


def test_budgets_within_allocation() -> None:
    for b in _load("org.json")["budgets"]:
        assert b["committed"] + b["spent"] <= b["allocated"]


def test_poisoned_docs_are_registered() -> None:
    index = _load("docs/index.json")
    assert sum(e["poisoned"] for e in index) >= 3
