// Contract check against the live gateway, using the app's own SSE parser (lib/sse.ts).
//   node scripts/verify.ts            (Node >= 22.18: TypeScript types are stripped natively)
// Read-only except: one feedback row; proposals are created and then cancelled (nothing executes).

import { streamSse, type SseEvent } from "../lib/sse.ts";

const G = (process.env.NEXT_PUBLIC_GATEWAY_URL ?? "http://127.0.0.1:8080").replace(/\/$/, "");
const problems: string[] = [];
let checks = 0;

function expect(cond: unknown, what: string) {
  checks++;
  if (!cond) problems.push(what);
}

function keys(obj: unknown, required: string[], where: string) {
  const o = (obj ?? {}) as Record<string, unknown>;
  for (const k of required) expect(k in o, `${where}: missing "${k}" (got ${Object.keys(o).join(", ") || "nothing"})`);
}

async function json(path: string, token?: string, body?: unknown): Promise<{ status: number; data: unknown; headers: Headers }> {
  for (let attempt = 0; ; attempt++) {
    try {
      const res = await fetch(`${G}${path}`, {
        method: body === undefined ? "GET" : "POST",
        headers: { "content-type": "application/json", origin: "http://localhost:3001", ...(token ? { authorization: `Bearer ${token}` } : {}) },
        body: body === undefined ? undefined : JSON.stringify(body),
      });
      const text = await res.text();
      let data: unknown = text;
      try {
        data = JSON.parse(text);
      } catch {
        /* not JSON */
      }
      return { status: res.status, data, headers: res.headers };
    } catch (e) {
      if (attempt >= 5) throw e; // the gateway restarts now and then while the backend is being worked on
      await new Promise((r) => setTimeout(r, 2000));
    }
  }
}

async function login(id: string): Promise<string> {
  const r = await json("/v1/auth/login", undefined, { user_id: id });
  return (r.data as { token: string }).token;
}

async function chat(token: string, message: string): Promise<SseEvent[]> {
  for (let attempt = 0; attempt < 15; attempt++) {
    const events: SseEvent[] = [];
    const status = await streamSse(`${G}/v1/chat`, { token, body: { message, history: [] } }, (e) => events.push(e));
    if (status === 200) return events;
    if (status !== 429) throw new Error(`chat HTTP ${status}`);
    await new Promise((r) => setTimeout(r, 2000));
  }
  throw new Error("chat rate-limited");
}

const tool = (token: string, name: string, args: Record<string, unknown> = {}) => json(`/v1/tools/${name}`, token, { args }).then((r) => r.data as Record<string, unknown>);

async function main() {
  // Auth + CORS ---------------------------------------------------------------------------------
  const personas = (await json("/v1/auth/personas")).data as Record<string, unknown>[];
  expect(Array.isArray(personas) && personas.length > 0, "personas: empty");
  keys(personas[0], ["id", "name", "email", "role", "department", "spend_limit", "approval_limit"], "personas[0]");
  const priya = await login("priya.sharma");
  const me = await json("/v1/me", priya);
  // /v1/me wraps the persona: {user, can_view_pii} (the app doesn't call it; login returns enough).
  const meUser = (me.data as { user?: Record<string, unknown> }).user ?? {};
  expect("role" in meUser, "/v1/me.user: no role");
  if (!("id" in meUser)) console.log(`note: /v1/me.user uses claim names (${Object.keys(meUser).join(", ")}), not the persona record`);
  expect(me.headers.get("access-control-allow-origin"), "CORS: no access-control-allow-origin for http://localhost:3001");

  // Chat SSE ------------------------------------------------------------------------------------
  const ev = await chat(priya, "What is the hotel cap per night in tier-1 cities?");
  const names = ev.map((e) => e.event);
  expect(names[0] === "meta", `chat: first event ${names[0]}`);
  expect(names.includes("delta") && names.at(-1) === "done", `chat: events ${names.join(",")}`);
  const meta = ev[0].data as { request_id?: string };
  expect(meta.request_id, "chat meta: no request_id");
  const sources = ev.find((e) => e.event === "sources")?.data as Record<string, unknown>[] | undefined;
  if (sources?.length) keys(sources[0], ["id", "chunk_id", "doc_id", "title", "section_path", "source", "snippet"], "chat sources[0]");
  const done = ev.at(-1)!.data as Record<string, unknown>;
  expect(!("diagnostics" in done), "chat: diagnostics leaked to a non-admin");
  const answer = ev.filter((e) => e.event === "delta").map((e) => (e.data as { text: string }).text).join("");
  console.log(`chat: ${names.join(" → ")} | status=${done.status} | ${answer.slice(0, 70)}…`);

  // Feedback ------------------------------------------------------------------------------------
  const fb = await json("/v1/feedback", priya, { request_id: meta.request_id, rating: "up", question: "What is the hotel cap per night in tier-1 cities?", answer: answer.slice(0, 500) });
  expect(fb.status === 200 && (fb.data as { ok?: boolean }).ok === true, `feedback: ${fb.status} ${JSON.stringify(fb.data)}`);

  // Tools used by the pages ---------------------------------------------------------------------
  const mine = await tool(priya, "list_my_requests");
  keys(mine, ["items", "count"], "list_my_requests");
  const first = (mine.items as Record<string, unknown>[])[0];
  if (first) {
    keys(first, ["id", "vendor", "total", "status", "created_at"], "list_my_requests.items[0]");
    const pr = await tool(priya, "get_purchase_request", { pr_id: first.id });
    keys(pr, ["id", "status", "total", "currency", "department_id", "justification", "vendor_name", "requester_name", "created_at", "updated_at", "approval_chain", "approvals", "line_items"], "get_purchase_request");
    const chain = await tool(priya, "get_approval_chain", { pr_id: first.id });
    keys(chain, ["pr_id", "status", "steps"], "get_approval_chain");
  }
  // A request with approvals, to check the step and approval shapes.
  const withChain = (mine.items as Record<string, unknown>[]).find((r) => String(r.status).startsWith("pending"));
  if (withChain) {
    const chain = await tool(priya, "get_approval_chain", { pr_id: withChain.id });
    const steps = chain.steps as Record<string, unknown>[];
    if (steps.length) keys(steps[0], ["level", "state", "by"], "get_approval_chain.steps[0]");
  }
  const spend = await tool(priya, "get_spend_summary", { department: "IT" });
  keys(spend, ["department", "fiscal_year", "allocated", "committed", "spent", "remaining", "monthly", "top_vendors"], "get_spend_summary");
  if ((spend.monthly as unknown[])?.length) keys((spend.monthly as unknown[])[0], ["month", "amount"], "get_spend_summary.monthly[0]");
  if ((spend.top_vendors as unknown[])?.length) keys((spend.top_vendors as unknown[])[0], ["vendor_id", "vendor", "amount"], "get_spend_summary.top_vendors[0]");
  const otherDept = await tool(priya, "get_spend_summary", { department: "HR" });
  expect(otherDept.status === "denied", `get_spend_summary: employee read another department (${JSON.stringify(otherDept).slice(0, 80)})`);

  // Approvals: propose and cancel (nothing changes) ---------------------------------------------
  const james = await login("james.chen");
  const pending = await tool(james, "list_pending_approvals");
  keys(pending, ["items", "count"], "list_pending_approvals");
  const p0 = (pending.items as Record<string, unknown>[])[0];
  if (p0) {
    keys(p0, ["id", "requester", "department_id", "vendor", "total", "status", "created_at"], "list_pending_approvals.items[0]");
    const prop = await tool(james, "approve_request", { pr_id: p0.id, comment: "verify script" });
    keys(prop, ["proposal_id", "action", "summary", "message", "status"], "approve_request proposal");
    keys(prop.summary, ["pr_id", "requester", "vendor", "total", "level", "decision", "approval_chain"], "approve_request summary");
    const c = await json(`/v1/actions/${prop.proposal_id}/cancel`, james, {});
    expect((c.data as { status?: string }).status === "cancelled", `cancel: ${JSON.stringify(c.data)}`);
  }

  // Create proposal through chat (the only route), then cancel ----------------------------------
  // Scripted tool-call form: the offline mock LLM cannot plan from natural language.
  const ce = await chat(priya, 'buy keyboards [[call create_purchase_request {"vendor_id": "V-0001", "items": [{"sku": "KB", "desc": "Keyboard", "qty": 2, "unit_price": 45.0}], "justification": "verify script"}]]');
  const proposal = ce.find((e) => e.event === "proposal")?.data as Record<string, unknown> | undefined;
  if (proposal) {
    keys(proposal, ["proposal_id", "action", "summary"], "chat proposal");
    if (!("message" in proposal)) console.log("note: chat proposal events carry no message/status (tool proposals do); the UI doesn't need them");
    keys(proposal.summary, ["vendor", "items", "total", "budget_remaining_before", "budget_remaining_after", "approval_chain"], "create proposal summary");
    const items = (proposal.summary as { items?: Record<string, unknown>[] }).items;
    if (items?.length) keys(items[0], ["sku", "desc", "qty", "unit_price"], "create proposal items[0]");
    await json(`/v1/actions/${proposal.proposal_id}/cancel`, priya, {});
  } else {
    console.log(`note: no proposal from chat create (events ${ce.map((e) => e.event).join(",")}); the offline mock may not plan this phrasing`);
  }

  // Admin Insights ------------------------------------------------------------------------------
  const admin = await login("olivia.grant");
  const ov = await json("/admin/metrics/overview?range=1h", admin);
  if (ov.status === 503) console.log("overview: 503 prometheus_unavailable (UI shows its designed empty state)");
  else {
    keys(ov.data, ["range", "step_seconds", "generated_at", "source", "kpis", "funnel", "latency_stages", "router", "providers", "cost_series", "cache_series", "gate_kept", "gate_relevance", "guard_blocks"], "overview");
    const o = ov.data as Record<string, unknown>;
    const kpis = o.kpis as Record<string, unknown>[];
    if (kpis?.length) keys(kpis[0], ["id", "label", "unit", "value", "previous", "series"], "overview.kpis[0]");
    keys(o.router, ["by_intent", "by_tier"], "overview.router");
    keys(o.providers, ["usage", "fallbacks", "fallback_series"], "overview.providers");
    keys(o.cost_series, ["actual", "shadow"], "overview.cost_series");
    keys(o.cache_series, ["l1", "l2", "semantic", "miss"], "overview.cache_series");
    console.log(`overview: ${kpis.length} KPIs, ${(o.latency_stages as unknown[]).length} stages, funnel ${JSON.stringify(o.funnel).slice(0, 120)}`);
  }
  const forbidden = await json("/admin/metrics/overview?range=1h", priya);
  expect(forbidden.status === 403, `overview as employee: HTTP ${forbidden.status}`);

  const evals = await json("/admin/evals", admin);
  keys(evals.data, ["generated_at", "source", "runs", "baseline", "tolerances", "redteam", "cache_check", "agent_tasks", "judges"], "evals");
  const e = evals.data as Record<string, unknown>;
  const runs = e.runs as Record<string, unknown>[];
  if (runs?.length) {
    keys(runs[0], ["run_id", "created_at", "git_sha", "label", "passed", "n_cases", "metrics", "hard_gates"], "evals.runs[0]");
    const raw = await json(`/admin/evals/${runs[0].run_id}`, admin);
    expect(raw.status === 200, `evals/{run_id}: HTTP ${raw.status}`);
  }
  if (e.redteam) keys(e.redteam, ["run_id", "passed", "hard_gates", "block_rates", "benign_fpr", "timing", "confusion"], "evals.redteam");
  if (e.redteam) keys((e.redteam as Record<string, unknown>).confusion, ["tp", "fn", "fp", "tn"], "evals.redteam.confusion");
  console.log(`evals: ${runs?.length ?? 0} runs, tolerances ${Object.keys((e.tolerances as object) ?? {}).join(",")}, redteam ${e.redteam ? "yes" : "no"}`);

  const ex = await json("/admin/experiments", admin);
  keys(ex.data, ["generated_at", "source", "experiments"], "experiments");

  const tr = await json("/admin/traces?limit=50", admin);
  keys(tr.data, ["items"], "traces");
  const items = (tr.data as { items: Record<string, unknown>[] }).items;
  if (items?.length) {
    keys(items[0], ["request_id", "trace_id", "at", "role", "status", "reason", "total_ms", "cache_layer", "tier", "intent"], "traces.items[0]");
    const d = await json(`/admin/traces/${items[0].request_id}`, admin);
    keys(d.data, ["request_id", "trace_id", "at", "status", "total_ms", "spans", "diagnostics", "grafana_url"], "traces/{id}");
    const spans = (d.data as { spans: unknown[] }).spans;
    if (spans?.length) keys(spans[0], ["name", "start_ms", "duration_ms"], "traces/{id}.spans[0]");
    console.log(`traces: ${items.length} items, first has ${spans?.length ?? 0} spans`);
  }

  console.log(`\n${checks} checks, ${problems.length} problems`);
  for (const p of problems) console.log(`  ✗ ${p}`);
  process.exit(problems.length ? 1 : 0);
}

main().catch((e) => {
  console.error(e);
  process.exit(2);
});
