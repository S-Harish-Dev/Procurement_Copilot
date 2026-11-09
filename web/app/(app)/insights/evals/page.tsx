"use client";

import { useQuery } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { Area, Bar, BarChart, CartesianGrid, ComposedChart, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartCard } from "@/components/charts/chart-card";
import { AXIS, BAR_H, ChartTooltip, GRID, LINE } from "@/components/charts/common";
import { Card, PageHeader } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { PassChip } from "@/components/ui/status";
import { get } from "@/lib/api";
import { count, dateTime, pct } from "@/lib/format";
import type { EvalRun, Evals, MetricCI } from "@/lib/types";

const SOURCE = "evals/reports (make eval-fast, redteam, cache-check, agent-tasks)";
const METRICS: { key: string; label: string; higher: boolean }[] = [
  { key: "recall@20", label: "Recall@20", higher: true },
  { key: "hit@5", label: "Hit@5", higher: true },
  { key: "mrr", label: "MRR", higher: true },
  { key: "ndcg@10", label: "nDCG@10", higher: true },
  { key: "guard_detection", label: "Injection detection", higher: true },
  { key: "guard_benign_fpr", label: "False alarms on normal questions", higher: false },
];
const CATEGORY_LABEL: Record<string, string> = {
  direct_injection: "Direct injection",
  prompt_extraction: "Prompt extraction",
  jailbreak_roleplay: "Jailbreak role-play",
  canary_extraction: "Canary extraction",
  indirect_injection: "Indirect (poisoned docs)",
  cross_role_exfil: "Cross-role exfiltration",
  pii_extraction: "PII extraction",
  tool_abuse: "Tool abuse",
};

/** The value a metric must not cross: baseline minus tolerance (or plus, for lower-is-better). */
function gateLine(e: Evals, key: string): number | null {
  const base = e.baseline?.metrics[key]?.mean;
  const tol = e.tolerances[key];
  if (base === undefined || !tol) return null;
  return tol.higher_is_better ? base - tol.tolerance : base + tol.tolerance;
}

function regressed(e: Evals, key: string, m?: MetricCI): boolean | null {
  const line = gateLine(e, key);
  if (line === null || !m) return null;
  return e.tolerances[key].higher_is_better ? m.mean < line : m.mean > line;
}

export default function EvalsPage() {
  const q = useQuery({ queryKey: ["evals"], queryFn: () => get<Evals>("/admin/evals") });
  return (
    <>
      <PageHeader icon={ShieldCheck} tone="violet" title="Evals" description="Every change is measured before it ships: retrieval quality, guardrails, cache isolation and agent actions." />
      {q.isPending ? <Skeleton className="h-96" /> : q.isError ? <ErrorState error={q.error} onRetry={() => q.refetch()} /> : <EvalsBody e={q.data} />}
    </>
  );
}

function EvalsBody({ e }: { e: Evals }) {
  const runs = [...e.runs].sort((a, b) => a.created_at.localeCompare(b.created_at));
  const latest = runs[runs.length - 1];
  return (
    <div className="space-y-6">
      <section aria-labelledby="hist">
        <h2 id="hist" className="mb-3 text-lg font-semibold">
          Run history
        </h2>
        {runs.length === 0 ? (
          <EmptyState title="No eval runs yet">Run make eval-fast to record one.</EmptyState>
        ) : (
          <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
            {METRICS.map((m) => (
              <MetricHistory key={m.key} e={e} runs={runs} metric={m} />
            ))}
          </div>
        )}
      </section>

      {latest && <DiffTable e={e} latest={latest} />}

      <RedTeamSection e={e} />

      <div className="grid gap-4 md:grid-cols-2">
        <SummaryCard
          title="Cache isolation"
          run={e.cache_check}
          lines={e.cache_check ? Object.entries(e.cache_check.hard_gates).map(([k, v]) => [k.replaceAll("_", " "), v]) : []}
          empty="Run make cache-check."
        />
        <SummaryCard
          title="Agent tasks"
          run={e.agent_tasks}
          lines={
            e.agent_tasks
              ? Object.entries(e.agent_tasks.summary).map(([k, v]) => [
                  k.replaceAll("_", " "),
                  typeof v === "number" ? (k.includes("rate") ? pct(v, 1) : v) : JSON.stringify(v),
                ])
              : []
          }
          empty="Run make agent-tasks."
        />
      </div>

      <section aria-labelledby="judges">
        <h2 id="judges" className="mb-3 text-lg font-semibold">
          Answer quality judges
        </h2>
        {e.judges ? (
          <pre className="overflow-auto rounded-lg bg-surface-2 p-3 text-xs">{JSON.stringify(e.judges, null, 2)}</pre>
        ) : (
          <EmptyState title="No judged runs yet">
            Faithfulness, correctness, abstention accuracy and the agreement between the two judges (with Cohen’s κ) appear after the first
            make eval-full run with real model API keys. The offline stack uses a mock model, so judging it would measure the mock.
          </EmptyState>
        )}
      </section>
      <p className="text-xs text-ink-2">
        Source: {SOURCE} · updated {dateTime(e.generated_at)}
      </p>
    </div>
  );
}

function MetricHistory({ e, runs, metric }: { e: Evals; runs: EvalRun[]; metric: { key: string; label: string; higher: boolean } }) {
  const data = runs
    .filter((r) => r.metrics[metric.key])
    .map((r, i) => ({ i: i + 1, at: r.created_at, run: r.run_id, mean: r.metrics[metric.key].mean, band: [r.metrics[metric.key].ci_low, r.metrics[metric.key].ci_high] as [number, number] }));
  const line = gateLine(e, metric.key);
  const last = data[data.length - 1];
  const fine = Math.max(...data.map((d) => d.band[1]), line ?? 0) < 0.1;
  const ok = last && line !== null ? (metric.higher ? last.mean >= line : last.mean <= line) : null;
  const title = last
    ? `${metric.label} ${pct(last.mean, 1)}${ok === null ? "" : ok ? ", inside the gate" : ", outside the gate"}`
    : metric.label;
  return (
    <ChartCard
      title={title}
      subtitle={`${data.length} runs · shaded band = 95% bootstrap CI`}
      howTo={`Each point is one eval run, oldest left. The shaded band is the 95% confidence interval. The reference line is the regression gate: ${metric.higher ? "a run below it fails" : "a run above it fails"} (baseline ${metric.higher ? "minus" : "plus"} tolerance).`}
      source={SOURCE}
      updated={e.generated_at}
      rows={data.map((d) => ({ run: d.run, at: d.at, mean: d.mean, ci_low: d.band[0], ci_high: d.band[1], gate: line }))}
      raw={data}
      filename={`history-${metric.key}`}
      empty={<EmptyState title="Not measured in these runs" />}
    >
      <div className="h-44">
        <ResponsiveContainer>
          <ComposedChart data={data} margin={{ top: 8, right: 8, left: 0 }}>
            <CartesianGrid {...GRID} />
            <XAxis dataKey="i" {...AXIS} tickFormatter={(i: number) => `#${i}`} minTickGap={16} />
            {/* Ratios: never past 100%; small scales (false alarms) need a decimal or ticks repeat. */}
            <YAxis
              {...AXIS}
              domain={[(min: number) => Math.max(0, min - 0.02), (max: number) => Math.min(1, max + 0.02)]}
              tickFormatter={(v: number) => pct(v, fine ? 1 : 0)}
              width={48}
            />
            <Tooltip
              content={
                <ChartTooltip
                  format={(v, name) => (name === "95% CI" ? "" : pct(v, 1))}
                  labelFormat={(l) => {
                    const d = data[Number(l) - 1];
                    return d ? `Run #${l} · ${dateTime(d.at)}` : String(l);
                  }}
                />
              }
            />
            <Area dataKey="band" name="95% CI" stroke="none" fill="var(--series-1)" fillOpacity={0.1} isAnimationActive={false} />
            {line !== null && <ReferenceLine y={line} stroke="var(--status-critical)" strokeWidth={1} label={{ value: "gate", position: "insideTopRight", fill: "var(--ink-2)", fontSize: 11 }} />}
            <Line dataKey="mean" name={metric.label} stroke="var(--series-1)" {...LINE} dot={{ r: 3, fill: "var(--series-1)", stroke: "var(--surface)", strokeWidth: 1.5 }} isAnimationActive={false} />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
    </ChartCard>
  );
}

function DiffTable({ e, latest }: { e: Evals; latest: EvalRun }) {
  return (
    <section aria-labelledby="diff">
      <h2 id="diff" className="mb-1 text-lg font-semibold">
        Latest run vs baseline
      </h2>
      <p className="mb-3 text-sm text-ink-2">
        Run {latest.run_id} ({dateTime(latest.created_at)}, {latest.git_sha?.slice(0, 7)}) against baseline {e.baseline?.label ?? e.baseline?.run_id ?? "—"}.
      </p>
      <Card className="overflow-x-auto">
        <table className="w-full text-sm">
          <caption className="sr-only">Latest run compared with the baseline</caption>
          <thead className="text-left text-xs text-ink-2">
            <tr className="border-b border-line">
              <th scope="col" className="px-4 py-2 font-medium">Metric</th>
              <th scope="col" className="px-4 py-2 text-right font-medium">Latest</th>
              <th scope="col" className="px-4 py-2 text-right font-medium">95% CI</th>
              <th scope="col" className="px-4 py-2 text-right font-medium">Baseline</th>
              <th scope="col" className="px-4 py-2 text-right font-medium">Change</th>
              <th scope="col" className="px-4 py-2 font-medium">Gate</th>
            </tr>
          </thead>
          <tbody className="tabular">
            {METRICS.map((m) => {
              const cur = latest.metrics[m.key];
              const base = e.baseline?.metrics[m.key];
              const reg = regressed(e, m.key, cur);
              return (
                <tr key={m.key} className="border-b border-line last:border-0">
                  <th scope="row" className="px-4 py-2 text-left font-normal">{m.label}</th>
                  <td className="px-4 py-2 text-right">{cur ? pct(cur.mean, 1) : "—"}</td>
                  <td className="px-4 py-2 text-right text-ink-2">{cur ? `${pct(cur.ci_low, 1)}–${pct(cur.ci_high, 1)}` : "—"}</td>
                  <td className="px-4 py-2 text-right">{base ? pct(base.mean, 1) : "—"}</td>
                  <td className="px-4 py-2 text-right">{cur && base ? `${cur.mean - base.mean >= 0 ? "+" : "−"}${(Math.abs(cur.mean - base.mean) * 100).toFixed(1)} pts` : "—"}</td>
                  <td className="px-4 py-2">{reg === null ? <span className="text-ink-2">no gate</span> : <PassChip pass={!reg} />}</td>
                </tr>
              );
            })}
            {Object.entries(latest.hard_gates).map(([k, v]) => (
              <tr key={k} className="border-b border-line last:border-0">
                <th scope="row" className="px-4 py-2 text-left font-normal">{k.replaceAll("_", " ")} (hard gate, must be 0)</th>
                <td className="px-4 py-2 text-right">{v}</td>
                <td colSpan={3} />
                <td className="px-4 py-2">
                  <PassChip pass={v === 0} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </section>
  );
}

function RedTeamSection({ e }: { e: Evals }) {
  const r = e.redteam;
  if (!r) {
    return (
      <section>
        <h2 className="mb-3 text-lg font-semibold">Red team</h2>
        <EmptyState title="No red-team run yet">Run make redteam.</EmptyState>
      </section>
    );
  }
  const rates = Object.entries(r.block_rates)
    .map(([k, v]) => ({ category: CATEGORY_LABEL[k] ?? k, key: k, ...v, pct: v.rate * 100 }))
    .sort((a, b) => b.rate - a.rate);
  const c = r.confusion;
  const total = c.tp + c.fn + c.fp + c.tn;
  const cells = [
    { label: "Attack, blocked", v: c.tp, good: true },
    { label: "Attack, let through", v: c.fn, good: false },
    { label: "Normal, blocked", v: c.fp, good: false },
    { label: "Normal, let through", v: c.tn, good: true },
  ];
  const max = Math.max(...cells.map((x) => x.v), 1);
  return (
    <section aria-labelledby="rt">
      <h2 id="rt" className="mb-1 text-lg font-semibold">
        Red team
      </h2>
      <p className="mb-3 text-sm text-ink-2">
        Run {r.run_id}. Leaks, unauthorized actions and the other hard gates must all be zero; block rates are context, since several attack types are
        stopped later (permissions, masking, confirmation) rather than at the input.
      </p>
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-4">
          <h3 className="font-semibold">Hard gates</h3>
          <ul className="mt-3 space-y-2 text-sm">
            {Object.entries(r.hard_gates).map(([k, v]) => (
              <li key={k} className="flex items-center justify-between gap-2">
                <span>{k.replaceAll("_", " ")}</span>
                <span className="flex items-center gap-2">
                  <span className="tabular font-semibold">{v}</span>
                  <PassChip pass={v === 0} />
                </span>
              </li>
            ))}
          </ul>
          <p className="mt-4 text-sm">
            False alarms on normal questions: <span className="font-semibold">{pct(r.benign_fpr, 1)}</span>
          </p>
        </Card>
        <ChartCard
          className="lg:col-span-2"
          title={`${rates.filter((x) => x.rate === 1).length} of ${rates.length} attack types are blocked at the input every time`}
          subtitle="Share of attacks stopped by the input guard, by attack type, %"
          howTo="Input blocking is only one defence. Cross-role, PII, poisoned-document and tool-abuse attacks are expected to pass the input guard; they are stopped by permissions, masking, quarantine and confirmation, which the hard gates check."
          source={SOURCE}
          updated={e.generated_at}
          rows={rates.map((x) => ({ category: x.key, blocked: x.blocked, n: x.n, rate: x.rate }))}
          raw={r.block_rates}
          filename="redteam-block-rates"
        >
          <div style={{ height: rates.length * 32 + 30 }}>
            <ResponsiveContainer>
              <BarChart data={rates} layout="vertical" margin={{ left: 8, right: 48 }}>
                <CartesianGrid {...GRID} horizontal={false} vertical />
                <XAxis type="number" domain={[0, 100]} {...AXIS} tickFormatter={(v: number) => `${v}%`} />
                <YAxis type="category" dataKey="category" {...AXIS} width={170} />
                <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip format={(v) => `${v.toFixed(0)}%`} />} />
                <Bar dataKey="pct" name="Blocked" fill="var(--series-1)" {...BAR_H} isAnimationActive={false} label={{ position: "right", fill: "var(--ink-2)", fontSize: 12, formatter: (v: unknown) => `${Number(v).toFixed(0)}%` }} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
        <ChartCard
          className="lg:col-span-3"
          title={`The injection detector caught ${pct(c.tp / Math.max(c.tp + c.fn, 1))} of input attacks with ${pct(c.fp / Math.max(c.fp + c.tn, 1), 1)} false alarms`}
          subtitle={`Confusion matrix over ${count(total)} prompts (attack categories aimed at the input guard, plus normal questions)`}
          howTo="Rows are what the prompt really was, columns what the guard did. Darker cells hold more prompts. The two cells to keep small are attacks let through and normal questions blocked."
          source={SOURCE}
          updated={e.generated_at}
          rows={cells.map((x) => ({ cell: x.label, count: x.v }))}
          raw={c}
          filename="injection-confusion"
        >
          <table className="mx-auto w-full max-w-xl border-separate border-spacing-[2px] text-sm">
            <caption className="sr-only">Injection detector confusion matrix</caption>
            <thead>
              <tr>
                <td />
                <th scope="col" className="pb-1 text-xs font-medium text-ink-2">Blocked</th>
                <th scope="col" className="pb-1 text-xs font-medium text-ink-2">Let through</th>
              </tr>
            </thead>
            <tbody>
              {[
                ["Attack", cells[0], cells[1]],
                ["Normal question", cells[2], cells[3]],
              ].map(([rowLabel, a, b]) => (
                <tr key={rowLabel as string}>
                  <th scope="row" className="pr-2 text-right text-xs font-medium text-ink-2">{rowLabel as string}</th>
                  {[a, b].map((cell) => {
                    const x = cell as (typeof cells)[number];
                    const dark = x.v / max > 0.55;
                    return (
                      <td
                        key={x.label}
                        className="h-20 rounded text-center"
                        style={{ background: x.v === 0 ? "var(--surface-2)" : dark ? "var(--seq-550)" : "var(--seq-250)", color: dark ? "#ffffff" : "#0b0b0b" }}
                      >
                        <span className="block text-xl font-semibold">{x.v}</span>
                        <span className="block text-xs">{x.label}</span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        </ChartCard>
      </div>
    </section>
  );
}

function SummaryCard({ title, run, lines, empty }: { title: string; run: { run_id: string; passed: boolean } | null; lines: [string, unknown][]; empty: string }) {
  return (
    <Card className="p-4">
      <div className="flex items-center justify-between gap-2">
        <h3 className="font-semibold">{title}</h3>
        {run && <PassChip pass={run.passed} label={run.passed ? "Passed" : "Failed"} />}
      </div>
      {run ? (
        <>
          <p className="text-xs text-ink-2">Run {run.run_id}</p>
          <ul className="mt-3 space-y-1 text-sm">
            {lines.map(([k, v]) => (
              <li key={k} className="flex justify-between gap-2">
                <span className="text-ink-2 capitalize">{k}</span>
                <span className="tabular">{String(v)}</span>
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p className="mt-2 text-sm text-ink-2">No run yet. {empty}</p>
      )}
    </Card>
  );
}
