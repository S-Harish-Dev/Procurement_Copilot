package acme.authz_test

import data.acme.authz

# --- fixtures ---------------------------------------------------------------
emp_it := {"id": "u-emp-it", "role": "employee", "dept": "IT", "spend_limit": 5000, "approval_limit": 0}

emp_legal := {"id": "u-emp-legal", "role": "employee", "dept": "Legal", "spend_limit": 5000, "approval_limit": 0}

mgr_it := {"id": "u-mgr-it", "role": "manager", "dept": "IT", "spend_limit": 25000, "approval_limit": 25000}

mgr_hr := {"id": "u-mgr-hr", "role": "manager", "dept": "HR", "spend_limit": 25000, "approval_limit": 25000}

fin := {"id": "u-fin", "role": "finance", "dept": "Finance", "spend_limit": 100000, "approval_limit": 100000}

fin_in_it := {"id": "u-fin-it", "role": "finance", "dept": "IT", "spend_limit": 100000, "approval_limit": 100000}

adm := {"id": "u-adm", "role": "admin", "dept": "Operations", "spend_limit": 1000000, "approval_limit": 10000000}

doc(user, classification, dept) := {"user": user, "action": "read_doc", "resource": {"type": "document", "classification": classification, "dept": dept}}

pr(user, action, amount, level, requester) := {"user": user, "action": action, "resource": {"type": "purchase_request", "amount": amount, "dept": "IT", "level": level, "requester_id": requester}}

# --- documents ----------------------------------------------------------------
test_public_doc_visible_to_employee if authz.allow with input as doc(emp_it, "public", "all")

test_internal_doc_same_dept if authz.allow with input as doc(emp_it, "internal", "IT")

test_internal_doc_company_wide if authz.allow with input as doc(emp_it, "internal", "all")

test_internal_doc_other_dept_denied if not authz.allow with input as doc(emp_it, "internal", "HR")

test_confidential_denied_for_it_employee if not authz.allow with input as doc(emp_it, "confidential", "Finance")

test_confidential_allowed_for_legal_dept if authz.allow with input as doc(emp_legal, "confidential", "Finance")

# The case a flat classifications x departments list gets wrong.
test_confidential_any_dept_for_finance_role_outside_finance if authz.allow with input as doc(fin_in_it, "confidential", "Legal")

test_restricted_denied_for_finance if not authz.allow with input as doc(fin, "restricted", "all")

test_restricted_allowed_for_admin if authz.allow with input as doc(adm, "restricted", "all")

test_admin_sees_other_dept_internal if authz.allow with input as doc(adm, "internal", "HR")

test_doc_grants_shape_for_employee if {
	g := authz.doc_grants with input as {"user": emp_it, "action": "retrieve"}
	g == {"public": ["*"], "internal": ["IT", "all"]}
}

test_unknown_classification_denied if not authz.allow with input as doc(adm, "top-secret", "all")

# --- tools ----------------------------------------------------------------------
test_employee_cannot_list_pending_approvals if not authz.allow with input as {"user": emp_it, "action": "call_tool", "resource": {"type": "tool", "name": "list_pending_approvals"}}

test_manager_can_list_pending_approvals if authz.allow with input as {"user": mgr_it, "action": "call_tool", "resource": {"type": "tool", "name": "list_pending_approvals"}}

test_unknown_tool_denied if not authz.allow with input as {"user": adm, "action": "call_tool", "resource": {"type": "tool", "name": "drop_tables"}}

test_write_tool_requires_confirmation if authz.requires_confirmation with input as {"user": emp_it, "action": "call_tool", "resource": {"type": "tool", "name": "create_purchase_request"}}

test_read_tool_no_confirmation if not authz.requires_confirmation with input as {"user": emp_it, "action": "call_tool", "resource": {"type": "tool", "name": "get_vendor"}}

# The spend summary is gated twice: the tool for every role, the data by view_budget.
test_spend_summary_tool_open_to_employees if authz.allow with input as {"user": emp_it, "action": "call_tool", "resource": {"type": "tool", "name": "get_spend_summary"}}

test_spend_summary_other_dept_denied if not authz.allow with input as {"user": emp_it, "action": "view_budget", "resource": {"dept": "HR"}}

# --- create PR --------------------------------------------------------------------
test_create_within_budget if authz.allow with input as {"user": emp_it, "action": "create_pr", "resource": {"amount": 4000, "dept": "IT", "budget_remaining": 4000}}

test_create_over_budget_denied if {
	r := authz.reasons with input as {"user": emp_it, "action": "create_pr", "resource": {"amount": 4000.01, "dept": "IT", "budget_remaining": 4000}}
	"exceeds_budget" in r
}

test_create_for_other_dept_denied if not authz.allow with input as {"user": emp_it, "action": "create_pr", "resource": {"amount": 10, "dept": "HR", "budget_remaining": 99999}}

test_create_zero_amount_denied if not authz.allow with input as {"user": emp_it, "action": "create_pr", "resource": {"amount": 0, "dept": "IT", "budget_remaining": 99999}}

# --- approval chain boundaries ----------------------------------------------------------
test_chain_at_auto_boundary if {
	c := authz.approval_chain with input as {"resource": {"amount": 1000}}
	c == []
}

test_chain_just_over_auto if {
	c := authz.approval_chain with input as {"resource": {"amount": 1000.01}}
	c == ["manager"]
}

test_chain_at_finance_boundary if {
	c := authz.approval_chain with input as {"resource": {"amount": 10000}}
	c == ["manager"]
}

test_chain_over_finance if {
	c := authz.approval_chain with input as {"resource": {"amount": 10001}}
	c == ["manager", "finance"]
}

test_chain_at_admin_boundary if {
	c := authz.approval_chain with input as {"resource": {"amount": 100000}}
	c == ["manager", "finance"]
}

test_chain_over_admin if {
	c := authz.approval_chain with input as {"resource": {"amount": 100001}}
	c == ["manager", "finance", "admin"]
}

# --- approve / reject ------------------------------------------------------------------
test_manager_approves_same_dept if authz.allow with input as pr(mgr_it, "approve_pr", 5000, "manager", emp_it.id)

test_self_approval_denied if {
	r := authz.reasons with input as pr(mgr_it, "approve_pr", 5000, "manager", mgr_it.id)
	"self_approval" in r
}

test_self_reject_denied if not authz.allow with input as pr(mgr_it, "reject_pr", 5000, "manager", mgr_it.id)

test_manager_other_dept_denied if {
	r := authz.reasons with input as pr(mgr_hr, "approve_pr", 5000, "manager", emp_it.id)
	"different_department" in r
}

test_employee_cannot_approve if not authz.allow with input as pr(emp_it, "approve_pr", 5000, "manager", "u-someone")

test_finance_covers_manager_level if authz.allow with input as pr(fin, "approve_pr", 5000, "manager", emp_it.id)

test_admin_cannot_act_at_finance_level if {
	r := authz.reasons with input as pr(adm, "approve_pr", 50000, "finance", emp_it.id)
	"wrong_level" in r
}

test_manager_cannot_act_at_finance_level if not authz.allow with input as pr(mgr_it, "approve_pr", 50000, "finance", emp_it.id)

# grace.kim-style manager: 8k is over her 5k limit and manager is the final level...
test_low_limit_manager_cannot_finalize if not authz.allow with input as pr(object.union(mgr_it, {"approval_limit": 5000}), "approve_pr", 8000, "manager", emp_it.id)

# ...so it is routed to Finance, who may finalize the manager level.
test_finance_finalizes_escalated_manager_level if authz.allow with input as pr(fin, "approve_pr", 8000, "manager", emp_it.id)

test_level_not_in_chain_denied if {
	r := authz.reasons with input as pr(fin, "approve_pr", 5000, "finance", emp_it.id)
	"level_not_in_chain" in r
}

# Manager limit is 25k but finance follows for 30k, so the manager may endorse.
test_manager_endorses_over_limit_when_finance_follows if authz.allow with input as pr(mgr_it, "approve_pr", 30000, "manager", emp_it.id)

# Manager is the final level for 8k; a manager with a 5k personal limit cannot finalize it.
test_final_level_over_personal_limit_denied if {
	low := object.union(mgr_it, {"approval_limit": 5000})
	r := authz.reasons with input as pr(low, "approve_pr", 8000, "manager", emp_it.id)
	"exceeds_approval_limit" in r
}

test_finance_finalizes_within_limit if authz.allow with input as pr(fin, "approve_pr", 100000, "finance", emp_it.id)

test_finance_endorses_when_admin_follows if authz.allow with input as pr(fin, "approve_pr", 150000, "finance", emp_it.id)

test_admin_finalizes_large if authz.allow with input as pr(adm, "approve_pr", 150000, "admin", emp_it.id)

# --- transactional reads ----------------------------------------------------------------
test_view_own_pr if authz.allow with input as {"user": emp_it, "action": "view_pr", "resource": {"requester_id": emp_it.id, "dept": "IT"}}

test_view_other_pr_denied if not authz.allow with input as {"user": emp_it, "action": "view_pr", "resource": {"requester_id": "u-x", "dept": "IT"}}

test_manager_views_dept_pr if authz.allow with input as {"user": mgr_it, "action": "view_pr", "resource": {"requester_id": emp_it.id, "dept": "IT"}}

test_budget_other_dept_denied if not authz.allow with input as {"user": mgr_it, "action": "view_budget", "resource": {"dept": "HR"}}

test_finance_views_any_budget if authz.allow with input as {"user": fin, "action": "view_budget", "resource": {"dept": "HR"}}

test_pii_denied_for_manager if not authz.allow with input as {"user": mgr_it, "action": "view_pii", "resource": {}}

test_unknown_action_denied if not authz.allow with input as {"user": adm, "action": "delete_everything", "resource": {}}
