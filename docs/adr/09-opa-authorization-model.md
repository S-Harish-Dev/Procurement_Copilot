# ADR-09: Authorization with OPA (Rego), per-classification retrieval grants, escalation to Finance

- Status: accepted (2026-10-30)

## Decision
OPA 1.22 sidecar evaluates `policies/authz.rego` (Rego v1) with all thresholds and grants in
`policies/data.json`. One query (`/v1/data/acme/authz`) returns `allow`, machine-readable `reasons`,
`doc_grants`, `approval_chain` and `requires_confirmation`. MCP servers re-check, and Postgres triggers are the
final backstop (self-approval, legal status transitions, one approver per level, append-only audit).

## Key design points
1. Retrieval scope = per-classification grants, e.g. `{"public":["*"], "internal":["IT","all"],
   "confidential":["*"]}`. The original two flat lists (classifications × departments) can't express
   "finance role sees confidential docs of *any* department but internal docs of only its own": the cross
   product is wrong in both directions. Each grant becomes one `should` clause in the Qdrant filter, applied
   to both dense and BM25 branches.
2. Approval chain: manager if > $1k; + finance if > $10k; + admin if > $100k (strict `>`, boundaries tested).
3. Personal approval limits bind only at the final level. A manager may endorse a $30k request (finance
   follows); a final-level approver over their limit gets `exceeds_approval_limit` → routed to Finance.
4. Finance may act at the manager level (departments without a manager, a manager's own request, the
   over-limit escalation). Separation of duties is kept by `UNIQUE (pr_id, approver_id)`: one person signs at
   most one level. Found by the synthetic-data generator, which enforces the same rules.
5. Deny by default; unknown action, tool or classification → deny. 52 unit tests (`opa test policies/`; 50 at M2, +2 for `get_spend_summary` in M8).

## Alternatives
- *Authorization in application code:* no audit trail of decisions, logic duplicated in Rust and Python.
- *Cedar / OpenFGA:* fine engines; OPA chosen for CNCF maturity, decision logs and Rego's data-driven policies.

## Open question
`spend_limit` is in the user model and JWT claims but the request check only looks at the remaining department budget when
creating a request, so it is currently not enforced. Confirm the intended meaning (per-request cap? P-Card limit?).
