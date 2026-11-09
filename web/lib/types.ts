// Shapes from docs/api.md (the contract with the gateway). Keep in sync with that file.

export type Role = "employee" | "manager" | "finance" | "admin";

export interface Persona {
  id: string;
  name: string;
  email: string;
  role: Role;
  department: string;
  spend_limit: number;
  approval_limit: number;
}

export interface Source {
  id: string;
  chunk_id: string;
  doc_id: string;
  title: string;
  section_path: string;
  source: string;
  snippet: string;
}

export interface LineItem {
  sku: string;
  desc: string;
  qty: number;
  unit_price: number;
}

export interface CreateSummary {
  vendor_id: string;
  vendor: string;
  items: LineItem[];
  total: number;
  department: string;
  budget_remaining_before: number;
  budget_remaining_after: number;
  approval_chain: string[];
  justification: string;
}

export interface DecisionSummary {
  pr_id: string;
  requester: string;
  vendor: string;
  total: number;
  level: string;
  decision: string;
  comment: string;
  approval_chain: string[];
}

export interface Proposal {
  proposal_id: string;
  action: string;
  summary: Partial<CreateSummary & DecisionSummary> & Record<string, unknown>;
  message: string;
  status: "proposal";
}

export type PrStatus =
  | "draft"
  | "pending_manager"
  | "pending_finance"
  | "pending_admin"
  | "approved"
  | "rejected"
  | "ordered"
  | "cancelled";

export interface RequestRow {
  id: string;
  vendor: string;
  total: number;
  status: PrStatus;
  created_at: string;
}

export interface PendingRow extends RequestRow {
  requester: string;
  department_id: string;
}

export interface PurchaseRequest {
  id: string;
  status: PrStatus;
  total: number;
  currency: string;
  department_id: string;
  justification: string;
  vendor_name: string;
  requester_name: string;
  created_at: string;
  updated_at: string;
  approval_chain: string[];
  approvals: { level: string; decision: "approved" | "rejected"; comment: string; decided_at: string; approver: string }[];
  line_items: { line_no: number; sku: string; description: string; qty: number; unit_price: number }[];
}

export interface ChainStep {
  level: string;
  state: "approved" | "rejected" | "waiting" | "upcoming";
  by: string | null;
}

export interface Budget {
  department: string;
  fiscal_year: number;
  allocated: number;
  committed: number;
  spent: number;
  remaining: number;
}

export interface SpendSummary extends Budget {
  monthly: { month: string; amount: number }[];
  top_vendors: { vendor_id: string; vendor: string; amount: number }[];
}

export interface Items<T> {
  items: T[];
  count: number;
}

/** Tool denials come back as HTTP 200 with this shape. */
export interface Denied {
  status: "denied";
  message: string;
}

export type Series = [number, number][];

export interface Kpi {
  id: string;
  label: string;
  unit: "count" | "seconds" | "ratio" | "usd";
  /** null = no data in the window (distinct from a measured 0). */
  value: number | null;
  previous: number | null;
  series: Series;
}

export interface Bucket {
  le: string;
  count: number;
}

export interface Overview {
  range: string;
  step_seconds: number;
  generated_at: string;
  source: string;
  kpis: Kpi[];
  funnel: { stage: string; count: number }[];
  latency_stages: { stage: string; p50_ms: number; p95_ms: number }[];
  router: { by_intent: { intent: string; count: number }[]; by_tier: { tier: string; count: number }[] };
  providers: {
    usage: { provider: string; requests: number }[];
    fallbacks: { from: string; to: string; count: number }[];
    fallback_series: Series;
  };
  cost_series: { actual: Series; shadow: Series };
  cache_series: { l1: Series; l2: Series; semantic: Series; miss: Series };
  gate_kept: Bucket[];
  gate_relevance: Bucket[];
  guard_blocks: { layer: string; count: number }[];
}

export interface MetricCI {
  mean: number;
  ci_low: number;
  ci_high: number;
  n: number;
}

export interface EvalRun {
  run_id: string;
  created_at: string;
  git_sha: string;
  label: string | null;
  passed: boolean;
  n_cases: number;
  metrics: Record<string, MetricCI>;
  hard_gates: Record<string, number>;
}

export interface RedTeam {
  run_id: string;
  passed: boolean;
  hard_gates: Record<string, number>;
  block_rates: Record<string, { blocked: number; n: number; rate: number }>;
  benign_fpr: number;
  timing: Record<string, number>;
  confusion: { tp: number; fn: number; fp: number; tn: number };
}

export interface Evals {
  generated_at: string;
  source: string;
  runs: EvalRun[];
  baseline: { run_id: string; label: string | null; metrics: Record<string, MetricCI> } | null;
  tolerances: Record<string, { tolerance: number; higher_is_better: boolean }>;
  redteam: RedTeam | null;
  cache_check: { run_id: string; passed: boolean; hard_gates: Record<string, number>; [k: string]: unknown } | null;
  agent_tasks: { run_id: string; passed: boolean; summary: Record<string, unknown> } | null;
  judges: unknown | null;
}

export interface Experiment {
  id: string;
  title: string;
  kind: string;
  created_at: string;
  takeaway: string;
  data: Record<string, unknown>;
}

export interface Experiments {
  generated_at: string;
  source: string;
  experiments: Experiment[];
}

export interface TraceRow {
  request_id: string;
  trace_id: string | null;
  at: string;
  role: string;
  status: string;
  reason: string | null;
  total_ms: number;
  cache_layer: string | null;
  tier: string | null;
  intent: string | null;
}

export interface TraceDetail {
  request_id: string;
  trace_id: string | null;
  at: string;
  status: string;
  total_ms: number;
  spans: { name: string; start_ms: number; duration_ms: number }[];
  diagnostics: Record<string, unknown>;
  grafana_url: string | null;
}
