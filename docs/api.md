# Gateway HTTP API (contract for the web UI)

Base URL `http://127.0.0.1:8080`. Every call except login/personas sends `Authorization: Bearer <jwt>`.
Errors are JSON `{"error": "<message>"}` with a 4xx/5xx status. Money is USD as a JSON number.
`/admin/*` requires role `admin` (403 otherwise).

## Auth
- `GET /v1/auth/personas` → `[{id, name, email, role, department, spend_limit, approval_limit}]`. Demo login cards.
- `POST /v1/auth/login {user_id}` → `{token, ...}`. `GET /v1/me` → `{user: {sub, name, role, dept, spend_limit,
  approval_limit}, can_view_pii}` (the UI uses the persona record from login instead).

## Chat (Server-Sent Events)
`POST /v1/chat {message, history: [{role: "user"|"assistant", content}]}` → `text/event-stream`, events in order:
- `meta` `{request_id}`
- `delta` `{text}`, repeated: append to the answer
- `replace` `{text}`: replace the whole answer shown so far (output guard withdrew it)
- `tool` `{name, phase: "started"}` then `{name, phase: "done", ok}`, twice per call: the agent used a tool
  (show a subtle "looked up …" chip)
- `proposal` `{proposal_id, action, summary}`: render an **ActionCard** (tool-route proposals from `/v1/tools`
  additionally carry `status: "proposal"` and `message`)
- `sources` `[{id: "S1", chunk_id, doc_id, title, section_path, source, snippet}]`: citations; answer text cites `[S1]`
- `done` `{status: "answered"|"no_answer"|"unavailable"|..., diagnostics?}` (`diagnostics` only for admins)

Proposal `summary` by action:
- `create_purchase_request`: `{vendor_id, vendor, items: [{sku, desc, qty, unit_price}], total, department,
  budget_remaining_before, budget_remaining_after, approval_chain: ["manager", "finance", ...], justification}`
- `approve_request` / `reject_request`: `{pr_id, requester, vendor, total, level, decision, comment, approval_chain}`

`POST /v1/actions/{proposal_id}/confirm` → SSE (`meta`, `tool`, `delta`, `done` with `status` `executed`|`denied`|...).
`POST /v1/actions/{proposal_id}/cancel` → `{status: "cancelled"}`.
`POST /v1/feedback {request_id, rating: "up"|"down", question, answer, comment?}` → `{ok: true}`. Text is PII-masked
before storage and becomes an eval candidate.

## Tools (UI data, same authorization as the agent)
`POST /v1/tools/{name} {args: {...}}` → the tool's JSON. Lists are `{items: [...], count}`. Denials are
`{status: "denied", message}` (HTTP 200: show `message`). Write tools only return a proposal (above).
- `list_my_requests {status?}` → items `{id, vendor, total, status, created_at}`
- `get_purchase_request {pr_id}` → `{id, status, total, currency, department_id, justification, vendor_name, requester_name,
  created_at, updated_at, approval_chain: [level], approvals: [{level, decision: "approved"|"rejected", comment,
  decided_at, approver}], line_items: [{line_no, sku, description, qty, unit_price}]}`. Build the status timeline
  from `created_at`, each approval's `decided_at`, and the current `status` (there is no separate history list).
- `get_approval_chain {pr_id}` → `{pr_id, status, steps: [{level, state: "approved"|"rejected"|"waiting"|"upcoming", by: name|null}]}`.
  An empty `approval_chain`/`steps` means auto-approved (no approval needed).
- `list_pending_approvals {}` (manager/finance/admin) → items `{id, requester, department_id, vendor, total, status, created_at}`
- `approve_request {pr_id, comment}` / `reject_request {pr_id, comment}` → proposal (then confirm)
- `get_budget {department?}` → `{department, fiscal_year, allocated, committed, spent, remaining}`
- `get_spend_summary {department?}` → `{department, fiscal_year, allocated, committed, spent, remaining,
  monthly: [{month: "2026-01", amount}], top_vendors: [{vendor_id, vendor, amount}]}` (spent = approved + ordered)
- PO PDF: `GET /v1/files/po/{pr_id}` → `application/pdf` (404 if none or not yours).
Status values: `draft, pending_manager, pending_finance, pending_admin, approved, rejected, ordered, cancelled`.
Departments: `IT, HR, Legal, Marketing, Operations, Finance`. Finance and admin may view every department's budget.

## Admin: Insights
All time series are `[[unix_seconds, value], ...]`. `range` ∈ `1h | 6h | 24h | 7d` (default `24h`).
Responses carry `generated_at` (RFC 3339) and `source` for the "data source + last updated" line.

`GET /admin/metrics/overview?range=24h` → (503 `{error: "prometheus_unavailable"}` if Prometheus is down)
```json
{
  "range": "24h", "step_seconds": 600, "generated_at": "...", "source": "prometheus",
  "kpis": [{"id": "requests|p95_latency_s|cache_hit_rate|cost_actual_usd|cost_shadow_usd|guard_blocks|jev_degraded_ratio|abstention_rate",
            "label": "Requests", "unit": "count|seconds|ratio|usd", "value": 123.0, "previous": 98.0, "series": [[t, v]]}],
  "funnel": [{"stage": "received|passed_guard|cache_hit|retrieved|answered|abstained|blocked", "count": 0}],
  "latency_stages": [{"stage": "guard", "p50_ms": 1.2, "p95_ms": 3.4}],
  "router": {"by_intent": [{"intent": "policy_qa", "count": 0}], "by_tier": [{"tier": "cheap|expensive", "count": 0}]},
  "providers": {"usage": [{"provider": "groq", "requests": 0}], "fallbacks": [{"from": "groq", "to": "gemini", "count": 0}],
                "fallback_series": [[t, v]]},
  "cost_series": {"actual": [[t, usd]], "shadow": [[t, usd]]},
  "cache_series": {"l1": [[t, hits]], "l2": [[t, hits]], "semantic": [[t, hits]], "miss": [[t, n]]},
  "gate_kept": [{"le": "0", "count": 0}, {"le": "1", "count": 0}, {"le": "+Inf", "count": 0}],
  "gate_relevance": [{"le": "0.1", "count": 0}, {"le": "1", "count": 0}],
  "guard_blocks": [{"layer": "regex|jev|classifier|ensemble|documents|output|action_integrity", "count": 0}]
}
```
Histogram buckets are **non-cumulative** counts per bucket (already differenced server-side). The funnel
branches after the guard: `cache_hit` requests skip retrieval, so `answered` (cache hits + answers after
retrieval, Q&A path only) can exceed `retrieved`. Agent turns are not in the funnel. Shadow cost prices every
answer at the expensive tier with no cache and no router (no Jev), so when Jev spend outweighs what routing and
caching save, actual > shadow, and the page should say so plainly.

`GET /admin/evals` →
```json
{
  "generated_at": "...", "source": "evals/reports",
  "runs": [{"run_id", "created_at", "git_sha", "label", "passed", "n_cases",
            "metrics": {"recall@20": {"mean", "ci_low", "ci_high", "n"}, "...": {}},
            "hard_gates": {"forbidden_doc_retrievals": 0, "acl_leaks": 0, "opa_test_failures": 0}}],
  "baseline": {"run_id", "label", "metrics": {}},
  "tolerances": {"recall@20": {"tolerance": 0.02, "higher_is_better": true}},
  "redteam": {"run_id", "passed", "hard_gates": {}, "block_rates": {"direct_injection": {"blocked", "n", "rate"}},
              "benign_fpr", "timing": {}, "confusion": {"tp", "fn", "fp", "tn"}} | null,
  "cache_check": {"run_id", "passed", "hard_gates": {}, "isolation": {}, "semantic": {}} | null,
  "agent_tasks": {"run_id", "passed", "summary": {}} | null,
  "judges": null
}
```
`judges` (Jev vs Gemini agreement, κ) stays `null` until real-LLM `eval-full` runs exist: show an empty state.
`GET /admin/evals/{run_id}` → the raw report JSON.

`GET /admin/experiments` → `{"generated_at", "source": "evals/experiments", "experiments": [{"id", "title", "kind", "created_at", "takeaway", "data": {...}}]}`
(M10 fills it; `kind` ∈ `embedders | fusion | tau_sweep | hnsw_ef | quantization | matryoshka | router_cost`).

`GET /admin/traces?limit=50` → `{"items": [{"request_id", "trace_id", "at", "role", "status", "reason", "total_ms", "cache_layer", "tier", "intent"}]}`
(newest first, in-memory ring of the last 1,000 chat requests).
`GET /admin/traces/{request_id}` → `{"request_id", "trace_id", "at", "status", "total_ms", "spans": [{"name", "start_ms", "duration_ms"}],
"diagnostics": {...}, "grafana_url": "..." | null}`.

`GET /admin/cache` / `POST /admin/cache/flush`, `GET /admin/runtime`, `POST /admin/users/{id}/reset-strikes`: operations.
