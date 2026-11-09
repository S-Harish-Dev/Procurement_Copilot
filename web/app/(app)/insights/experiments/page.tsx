"use client";

import { useQuery } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";
import { Bar, BarChart, CartesianGrid, Line, LineChart, ReferenceDot, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartCard, type Row } from "@/components/charts/chart-card";
import { AXIS, BAR_H, ChartTooltip, GRID, LINE, Legend } from "@/components/charts/common";
import { PageHeader } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { get } from "@/lib/api";
import { slot } from "@/lib/palette";
import type { Experiment, Experiments } from "@/lib/types";

/*
 * Experiments are rendered generically from two data shapes (documented in NOTES-M8.md):
 *   bars:   {bars: [{label, value}], unit?}                                  → horizontal bars
 *   curves: {curves: [{name, points: [[x, y], ...]}], x_label, y_label, chosen?: {x, y, label?}} → lines
 * Anything else falls back to the raw JSON view. Curves cap at 4 series (palette guidance).
 */
interface BarsData {
  bars: { label: string; value: number }[];
  unit?: string;
}
interface CurvesData {
  curves: { name: string; points: [number, number][] }[];
  x_label?: string;
  y_label?: string;
  chosen?: { x: number; y: number; label?: string };
}

const KIND_HELP: Record<string, string> = {
  embedders: "Retrieval quality per embedding model on the same eval set; longer is better.",
  fusion: "Dense only vs keyword (BM25) only vs hybrid fusion on the same eval set.",
  tau_sweep: "Precision against recall as the threshold moves; the marked point is the chosen threshold.",
  hnsw_ef: "Recall against latency as the vector-search effort (ef) grows, compared with exact search.",
  quantization: "int8 vs full-precision vectors: recall, memory and latency.",
  matryoshka: "Recall as the embedding is truncated to fewer dimensions.",
  router_cost: "Cost against answer quality for different routing thresholds.",
};

const fmt = (v: number, unit?: string) => (unit === "ratio" ? `${(v * 100).toFixed(1)}%` : unit === "ms" ? `${v.toFixed(1)} ms` : Number.isInteger(v) ? String(v) : v.toFixed(3));

function labelSide(x: number, curves: { points: [number, number][] }[]): "top" | "left" | "right" {
  const xs = curves.flatMap((c) => c.points.map((p) => p[0]));
  const lo = Math.min(...xs);
  const frac = (x - lo) / (Math.max(...xs) - lo || 1);
  return frac < 0.22 ? "right" : frac > 0.78 ? "left" : "top";
}

const MIN_HEAD = 22;
const MAX_HEAD = 52;

/**
 * Takeaways are long generated sentences. The card title is only the first clause (cut at the first
 * ; . : , or " vs " that is outside brackets and leaves at least MIN_HEAD characters, then capped at
 * MAX_HEAD on a word like "at"/"for"); everything after it, the supporting numbers, goes in the (i).
 */
function splitTakeaway(full: string): { headline: string; rest: string } {
  let depth = 0;
  let cut = -1;
  for (let i = 0; i < full.length && cut < 0; i++) {
    const c = full[i];
    if (c === "(") depth++;
    else if (c === ")") depth = Math.max(0, depth - 1);
    else if (depth === 0 && i >= MIN_HEAD && (/[;.:,]/.test(c) && /\s/.test(full[i + 1] ?? " ") ? true : full.startsWith(" vs ", i))) cut = i;
  }
  let headline = (cut < 0 ? full : full.slice(0, cut)).trim();
  if (headline.length > MAX_HEAD) {
    // Back up to the last connecting word that fits, so the title ends on a whole idea.
    const head = headline.slice(0, MAX_HEAD);
    const word = [...head.matchAll(/\s(at|for|with|and|on|in|to|vs)\s/g)].at(-1);
    headline = word && word.index > MIN_HEAD ? head.slice(0, word.index) : head.slice(0, head.lastIndexOf(" "));
  }
  return { headline, rest: full.slice(headline.length).replace(/^[\s;:,.]+/, "") };
}

/** "HNSW ef: recall vs latency (vs exact search)" -> subtitle "HNSW ef: recall vs latency", setup "vs exact search". */
function splitSubtitle(title: string): { subtitle: string; setup: string } {
  const m = /^(.*?)\s*\(([^)]*)\)\s*$/.exec(title);
  return m && title.length > 28 ? { subtitle: m[1], setup: m[2] } : { subtitle: title, setup: "" };
}

function ExperimentChart({ x, updated, wide }: { x: Experiment; updated: string; wide: boolean }) {
  const d = x.data as Partial<BarsData & CurvesData>;
  const { headline, rest } = splitTakeaway(x.takeaway || x.title);
  const { subtitle, setup } = splitSubtitle(x.title);
  const help = KIND_HELP[x.kind] ?? "See the raw JSON for this experiment's definition.";
  // Everything trimmed from the card face lives here: the numbers, the setup, then how to read it.
  const howTo = (
    <>
      {/* A detail that continues the title mid-sentence ("vs 0.895 at 768 …") gets a leading ellipsis. */}
      {rest && <span className="block font-medium text-ink">{/^(vs|at|for|and|with|on|in|to)\s/.test(rest) ? `… ${rest}` : rest}</span>}
      {setup && (
        <span className="mt-1 block">
          Setup: <span className="text-ink">{setup}</span>
        </span>
      )}
      <span className={`block ${rest || setup ? "mt-1.5" : ""}`}>{help}</span>
    </>
  );
  const common = { title: headline, subtitle, howTo, source: "evals/experiments", updated: x.created_at || updated, raw: x, filename: `experiment-${x.id}`, className: wide ? "lg:col-span-2" : "" };

  if (Array.isArray(d.bars)) {
    const bars = d.bars;
    const rows: Row[] = bars.map((b) => ({ label: b.label, value: b.value }));
    return (
      <ChartCard {...common} rows={rows}>
        {/* Fills the card (cards in a row share one height), never shorter than its bars need. */}
        <div className="flex-1" style={{ minHeight: bars.length * 34 + 30 }}>
          <ResponsiveContainer>
            <BarChart data={bars} layout="vertical" margin={{ left: 8, right: 56 }}>
              <CartesianGrid {...GRID} horizontal={false} vertical />
              <XAxis type="number" {...AXIS} tickFormatter={(v: number) => fmt(v, d.unit)} />
              <YAxis type="category" dataKey="label" {...AXIS} width={160} />
              <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip format={(v) => fmt(v, d.unit)} />} />
              <Bar dataKey="value" name={x.title} fill="var(--series-1)" {...BAR_H} isAnimationActive={false} label={{ position: "right", fill: "var(--ink-2)", fontSize: 12, formatter: (v: unknown) => fmt(Number(v), d.unit) }} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </ChartCard>
    );
  }

  if (Array.isArray(d.curves)) {
    const curves = d.curves.slice(0, 4);
    const rows: Row[] = curves.flatMap((c) => c.points.map(([px, py]) => ({ series: c.name, x: px, y: py })));
    return (
      <ChartCard {...common} rows={rows}>
        <div className="min-h-64 flex-1">
          <ResponsiveContainer>
            <LineChart margin={{ top: 22, right: 20, bottom: 16 }}>
              <CartesianGrid {...GRID} />
              <XAxis type="number" dataKey="x" {...AXIS} domain={["auto", "auto"]} label={{ value: d.x_label, position: "insideBottom", offset: -8, fill: "var(--ink-2)", fontSize: 12 }} />
              {/* Wide enough for the longest tick plus the rotated title, which otherwise sits on the numbers. */}
              <YAxis type="number" dataKey="y" {...AXIS} domain={["auto", "auto"]} width={72} label={{ value: d.y_label, angle: -90, position: "insideLeft", offset: 4, style: { textAnchor: "middle" }, fill: "var(--ink-2)", fontSize: 12 }} />
              <Tooltip content={<ChartTooltip format={(v) => fmt(v)} labelFormat={(l) => `${d.x_label ?? "x"} ${String(l)}`} />} />
              {curves.map((c, i) => (
                <Line key={c.name} data={c.points.map(([px, py]) => ({ x: px, y: py }))} dataKey="y" name={c.name} stroke={slot(i + 1)} {...LINE} dot={{ r: 3, fill: slot(i + 1), stroke: "var(--surface)", strokeWidth: 1.5 }} isAnimationActive={false} />
              ))}
              {d.chosen && (
                <ReferenceDot
                  x={d.chosen.x}
                  y={d.chosen.y}
                  r={6}
                  fill="var(--surface)"
                  stroke="var(--ink)"
                  strokeWidth={2}
                  // Label goes right of a dot near the left edge, left of one near the right edge, else above; a surface-coloured halo keeps it legible over lines.
                  label={{ value: d.chosen.label ?? "chosen", position: labelSide(d.chosen.x, curves), dy: labelSide(d.chosen.x, curves) === "top" ? 0 : -13, fill: "var(--ink)", fontSize: 12, stroke: "var(--surface)", strokeWidth: 4, paintOrder: "stroke" }}
                />
              )}
            </LineChart>
          </ResponsiveContainer>
        </div>
        <Legend shape="line" items={curves.map((c, i) => ({ label: c.name, color: slot(i + 1) }))} />
      </ChartCard>
    );
  }

  return (
    <ChartCard {...common} rows={[]} empty={<pre className="max-h-64 overflow-auto rounded bg-surface-2 p-2 text-xs">{JSON.stringify(x.data, null, 2)}</pre>}>
      {null}
    </ChartCard>
  );
}

export default function ExperimentsPage() {
  const q = useQuery({ queryKey: ["experiments"], queryFn: () => get<Experiments>("/admin/experiments") });
  return (
    <>
      <PageHeader icon={FlaskConical} tone="violet" title="Experiments" description="Ablations and threshold sweeps behind the configuration choices: each chart says what was chosen and why." />
      {q.isPending ? (
        <Skeleton className="h-80" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.data.experiments.length === 0 ? (
        <EmptyState title="No experiments recorded yet">
          Ablation and tuning runs (embedders, fusion, thresholds, vector-search effort, quantization, Matryoshka dimensions, router cost) write their results to evals/experiments and appear here.
        </EmptyState>
      ) : (
        <div className="grid gap-4 lg:grid-cols-2">
          {/* An odd last card spans both columns instead of leaving half a row empty. */}
          {q.data.experiments.map((x, i, all) => (
            <ExperimentChart key={x.id} x={x} updated={q.data.generated_at} wide={all.length % 2 === 1 && i === all.length - 1} />
          ))}
        </div>
      )}
    </>
  );
}
