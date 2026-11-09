# Acme procurement authorization policy.
#
# Query: POST /v1/data/acme/authz  with  {"input": {...}}
# Input shape:
#   user:     {id, role, dept, spend_limit, approval_limit}
#   action:   "retrieve" | "read_doc" | "call_tool" | "create_pr" | "approve_pr"
#             | "reject_pr" | "view_pr" | "view_budget" | "view_pii"
#   resource: action-specific (see each rule)
#
# Outputs (all evaluated in one call):
#   allow           bool — the decision for `action`
#   reasons         set of machine-readable deny reasons (empty when allowed)
#   doc_grants      {classification: [departments] | ["*"]} — retrieval scope for Qdrant filters
#   approval_chain  ordered levels required for resource.amount
#   requires_confirmation  true for write tools (the token itself is verified in the MCP server)
#
# Every rule is deny-by-default: `allow` is only true when a positive rule matches
# and no deny reason was produced.
package acme.authz

import data.acme

default allow := false

allow if {
	_permitted
	count(reasons) == 0
}

user := input.user

res := input.resource

# ---------------------------------------------------------------------------
# Document access
# ---------------------------------------------------------------------------

# Retrieval scope as explicit per-classification grants. A flat pair of lists
# (allowed_classifications x allowed_departments) cannot express "finance role
# sees confidential docs of ANY department but internal docs of only its own".
doc_grants["public"] := ["*"]

doc_grants["internal"] := ["*"] if user.role == "admin"

doc_grants["internal"] := [user.dept, "all"] if user.role != "admin"

doc_grants["confidential"] := ["*"] if _can_see_confidential

doc_grants["restricted"] := ["*"] if user.role == "admin"

_can_see_confidential if user.role in acme.confidential.roles

_can_see_confidential if user.dept in acme.confidential.departments

_doc_visible(classification, _) if "*" in doc_grants[classification]

_doc_visible(classification, dept) if dept in doc_grants[classification]

_permitted if input.action == "retrieve"

_permitted if {
	input.action == "read_doc"
	_doc_visible(res.classification, res.dept)
}

# ---------------------------------------------------------------------------
# Tools (role gate only; business rules are re-checked by the specific actions)
# ---------------------------------------------------------------------------

_permitted if {
	input.action == "call_tool"
	user.role in acme.tools[res.name].roles
}

requires_confirmation if {
	input.action == "call_tool"
	acme.tools[res.name].write
}

default requires_confirmation := false

# ---------------------------------------------------------------------------
# Purchase requests
# ---------------------------------------------------------------------------

# Anyone may raise a request for their own department, within remaining budget.
_permitted if input.action == "create_pr"

reasons contains "different_department" if {
	input.action == "create_pr"
	res.dept != user.dept
}

reasons contains "invalid_amount" if {
	input.action == "create_pr"
	not res.amount > 0
}

reasons contains "exceeds_budget" if {
	input.action == "create_pr"
	res.amount > res.budget_remaining
}

# Approval chain for an amount. ≤ auto_approve_max needs nobody.
approval_chain := [level |
	some level in ["manager", "finance", "admin"]
	_level_required(level, res.amount)
]

_level_required("manager", amount) if amount > acme.approval.auto_approve_max

_level_required("finance", amount) if amount > acme.approval.finance_threshold

_level_required("admin", amount) if amount > acme.approval.admin_threshold

# Approve/reject: the caller acts on the request's current pending `level`.
_permitted if input.action in {"approve_pr", "reject_pr"}

reasons contains "self_approval" if {
	input.action in {"approve_pr", "reject_pr"}
	res.requester_id == user.id
}

reasons contains "wrong_level" if {
	input.action in {"approve_pr", "reject_pr"}
	not _role_may_act(user.role, res.level)
}

_role_may_act(role, level) if role == level

# Finance covers the manager level: departments without a manager (Finance,
# Legal), a manager's own request, and a manager over their personal limit are
# all "routed to Finance". One person can still sign only ONE level of a request
# (UNIQUE (pr_id, approver_id) in the approvals table).
_role_may_act("finance", "manager")

reasons contains "level_not_in_chain" if {
	input.action in {"approve_pr", "reject_pr"}
	not res.level in approval_chain
}

# Managers only approve requests from their own department.
reasons contains "different_department" if {
	input.action in {"approve_pr", "reject_pr"}
	user.role == "manager"
	res.dept != user.dept
}

# A personal approval_limit only binds when this approver is the LAST level in
# the chain; otherwise a higher level still has to sign off after them.
# (e.g. a manager may endorse a $30k request because finance must also approve.)
reasons contains "exceeds_approval_limit" if {
	input.action == "approve_pr"
	res.level == approval_chain[count(approval_chain) - 1]
	res.amount > user.approval_limit
}

# ---------------------------------------------------------------------------
# Read access to transactional data
# ---------------------------------------------------------------------------

_privileged if user.role in {"finance", "admin"}

_permitted if {
	input.action == "view_pr"
	res.requester_id == user.id
}

_permitted if {
	input.action == "view_pr"
	user.role == "manager"
	res.dept == user.dept
}

_permitted if {
	input.action == "view_pr"
	_privileged
}

_permitted if {
	input.action == "view_budget"
	res.dept == user.dept
}

_permitted if {
	input.action == "view_budget"
	_privileged
}

_permitted if {
	input.action == "view_pii"
	user.role in acme.pii_roles
}
