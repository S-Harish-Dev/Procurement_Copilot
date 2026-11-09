"use client";

import { useQuery } from "@tanstack/react-query";
import { LayoutDashboard } from "lucide-react";
import { useState } from "react";
import { Area, AreaChart, Bar, BarChart, CartesianGrid, Cell, Line, LineChart, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartCard } from "@/components/charts/chart-card";
import { AXIS, BAR, BAR_H, ChartTooltip, GRID, LINE, Legend } from "@/components/charts/common";
import { StatTile } from "@/components/charts/stat-tile";
import { PageHeader } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { ApiError, get } from "@/lib/api";
import { count, money, ms, pct, timeTick } from "@/lib/format";
import { colorFor } from "@/lib/palette";
import { mergeSeries, RANGE_SECONDS, sumSeries } from "@/lib/series";
import type { Overview } from "@/lib/types";

const RANGES = ["1h", "6h", "24h", "7d"] as const;
const SOURCE = "Prometheus (gateway metrics)";
const STAGE_LABEL: Record<string, string> = {
  received: "Received",
  passed_guard: "Passed guard",
  cache_hit: "Cache hit",
  retrieved: "Retrieved",
  answered: "Answered",
  abstained: "Abstained",
  blocked: "Blocked",
};

function RangeFilter({ value, onChange }: { value: string; onChange: (r: string) => void }) {
  return (
    <div role="radiogroup" aria-label="Time range" className="inline-flex rounded-md border border-line bg-surface p-0.5">
      {RANGES.map((r) => (
        <button
          key={r}
          type="button"
          role="radio"
          aria-checked={value === r}
          onClick={() => onChange(r)}
          className={`rounded px-3 py-1 text-sm ${value === r ? "bg-violet-soft font-semibold text-violet-text" : "text-ink-2 hover:text-ink"}`}
        >
          {r === "7d" ? "7 days" : `Last ${r}`}
        </button>
      ))}
    </div>
  );
}

export default function OverviewPage() {
  const [range, setRange] = useState("24h");
  const q = useQuery({
    queryKey: ["overview", range],
    queryFn: () => get<Overview>(`/admin/metrics/overview?range=${range}`),
    placeholderData: (p) => p,
    refetchInterval: 60_000,
  });

  return (
    <>
      <PageHeader icon={LayoutDashboard} tone="violet" title="Overview" description="How the assistant is used, how fast it is, what it costs and what the guardrails stop." />
      {/* One filter row scopes every chart below. */}
      <div className="mb-5">
        <RangeFilter value={range} onChange={setRange} />
      </div>
      {q.isPending ? (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} className="h-28" />
          ))}
        </div>
      ) : q.isError ? (
        q.error instanceof ApiError && q.error.status === 503 ? (
          <EmptyState title="Metrics store is not reachable">
            The gateway answers, but Prometheus (which keeps its metrics over time) is down or not configured. Start it with the stack, then refresh.
          </EmptyState>
        ) : (
          <ErrorState error={q.error} onRetry={() => q.refetch()} />
        )
      ) : (
        <div className={q.isFetching && q.isPlaceholderData ? "opacity-60 transition-opacity" : ""}>
          <Dashboard o={q.data} />
        </div>
      )}
    </>
  );
}

function Dashboard({ o }: { o: Overview }) {
  const span = RANGE_SECONDS[o.range] ?? 86400;
  const tick = (t: number) => timeTick(t, span);
  const common = { source: SOURCE, updated: o.generated_at };

  // Funnel ----------------------------------------------------------------------------------------
  const funnel = o.funnel.map((f) => ({ stage: STAGE_LABEL[f.stage] ?? f.stage, count: f.count }));
  const received = o.funnel.find((f) => f.stage === "received")?.count ?? 0;
  const answered = o.funnel.find((f) => f.stage === "answered")?.count ?? 0;

  // Latency ---------------------------------------------------------------------------------------
  const lat = [...o.latency_stages].sort((a, b) => b.p95_ms - a.p95_ms);
  const slowest = lat[0];

  // Router ----------------------------------------------------------------------------------------
  const intents = o.router.by_intent.filter((i) => i.count > 0);
  const intentTotal = intents.reduce((a, i) => a + i.count, 0);
  const tiers = o.router.by_tier;
  const cheapShare = tiers.reduce((a, t) => a + t.count, 0) > 0 ? (tiers.find((t) => t.tier === "cheap")?.count ?? 0) / tiers.reduce((a, t) => a + t.count, 0) : 0;

  // Providers -------------------------------------------------------------------------------------
  const providers = o.providers.usage.filter((p) => p.requests > 0);
  const provTotal = providers.reduce((a, p) => a + p.requests, 0);
  const fallbacks = o.providers.fallbacks.reduce((a, f) => a + f.count, 0);
  const topProvider = [...providers].sort((a, b) => b.requests - a.requests)[0];

  // Cost ------------------------------------------------------------------------------------------
  const cost = mergeSeries({ actual: o.cost_series.actual, shadow: o.cost_series.shadow });
  const actual = sumSeries(o.cost_series.actual);
  const shadow = sumSeries(o.cost_series.shadow);
  const saved = shadow > 0 ? 1 - actual / shadow : 0;

  // Cache -----------------------------------------------------------------------------------------
  const cache = mergeSeries({ l1: o.cache_series.l1, l2: o.cache_series.l2, semantic: o.cache_series.semantic });
  const hits = sumSeries(o.cache_series.l1) + sumSeries(o.cache_series.l2) + sumSeries(o.cache_series.semantic);
  const lookups = hits + sumSeries(o.cache_series.miss);

  // Gate & guard ----------------------------------------------------------------------------------
  const kept = o.gate_kept.map((b) => ({ bucket: b.le === "+Inf" ? "more" : `≤ ${b.le}`, count: b.count }));
  const keptTotal = o.gate_kept.reduce((a, b) => a + b.count, 0);
  // Bucket bounds arrive as strings ("0", "0.0"); compare numerically.
  const keptZero = o.gate_kept.find((b) => b.le !== "+Inf" && Number(b.le) === 0)?.count ?? 0;
  const rel = o.gate_relevance.map((b) => ({ bucket: b.le === "+Inf" ? "> 1" : `≤ ${b.le}`, count: b.count }));
  const blocks = [...o.guard_blocks].sort((a, b) => b.count - a.count);
  const blockTotal = blocks.reduce((a, b) => a + b.count, 0);

  return (
    <div className="space-y-4">
      <div className="grid items-start gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {o.kpis.map((k) => (
          <StatTile key={k.id} kpi={k} />
        ))}
      </div>

      <div className="grid items-start gap-4 lg:grid-cols-2">
        <ChartCard
          {...common}
          title={received ? `${pct(answered / received)} of requests were answered` : "Request funnel"}
          subtitle="Requests at each stage of the pipeline, count"
          howTo="Read top to bottom: every request is received, most pass the input guard, then they are served from cache or retrieved. The last three bars are how retrieved requests ended."
          rows={o.funnel.map((f) => ({ stage: f.stage, count: f.count }))}
          raw={o.funnel}
          filename="funnel"
        >
          <div style={{ height: funnel.length * 34 + 30 }}>
            <ResponsiveContainer>
              <BarChart data={funnel} layout="vertical" margin={{ left: 8, right: 48 }}>
                <CartesianGrid {...GRID} horizontal={false} vertical />
                <XAxis type="number" {...AXIS} tickFormatter={count} />
                <YAxis type="category" dataKey="stage" {...AXIS} width={104} />
                <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip format={(v) => count(v)} />} />
                <Bar dataKey="count" name="Requests" fill="var(--series-1)" {...BAR_H} isAnimationActive={false} label={{ position: "right", fill: "var(--ink-2)", fontSize: 12 }} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>

        <ChartCard
          {...common}
          title={slowest ? `${slowest.stage} is the slowest stage (p95 ${ms(slowest.p95_ms)})` : "Latency by stage"}
          subtitle="Typical (p50) and slow (p95) time per stage, milliseconds"
          howTo="p50 is the median request; p95 is the slowest 5%. Stages run in sequence except the router and the injection classifier, which run in parallel."
          rows={o.latency_stages.map((s) => ({ stage: s.stage, p50_ms: s.p50_ms, p95_ms: s.p95_ms }))}
          raw={o.latency_stages}
          filename="latency-stages"
          empty={<EmptyState title="No requests in this range" />}
        >
          <div style={{ height: lat.length * 40 + 30 }}>
            <ResponsiveContainer>
              <BarChart data={lat} layout="vertical" margin={{ left: 8, right: 16 }} barGap={2}>
                <CartesianGrid {...GRID} horizontal={false} vertical />
                <XAxis type="number" {...AXIS} tickFormatter={(v: number) => ms(v)} />
                <YAxis type="category" dataKey="stage" {...AXIS} width={120} />
                <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip format={(v) => ms(v)} />} />
                <Bar dataKey="p50_ms" name="p50" fill="var(--series-1)" {...BAR_H} maxBarSize={12} isAnimationActive={false} />
                <Bar dataKey="p95_ms" name="p95" fill="var(--series-2)" {...BAR_H} maxBarSize={12} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
          <Legend items={[{ label: "p50", color: "var(--series-1)" }, { label: "p95", color: "var(--series-2)" }]} />
        </ChartCard>

        <ChartCard
          {...common}
          title={tiers.length ? `${pct(cheapShare)} of answers used the cheaper model` : "Router decisions"}
          subtitle="What each request was classified as, count"
          howTo="The router labels every request: a policy question, a data lookup, an action (create or approve), or out of scope. Questions then go to the cheap or the expensive model tier."
          rows={[...intents.map((i) => ({ kind: "intent", name: i.intent, count: i.count })), ...tiers.map((t) => ({ kind: "tier", name: t.tier, count: t.count }))]}
          raw={o.router}
          filename="router"
          empty={<EmptyState title="No routed requests in this range" />}
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <div className="h-44">
                <ResponsiveContainer>
                  <PieChart>
                    <Pie data={intents} dataKey="count" nameKey="intent" innerRadius="60%" outerRadius="90%" stroke="var(--surface)" strokeWidth={2} isAnimationActive={false}>
                      {intents.map((i) => (
                        <Cell key={i.intent} fill={colorFor(i.intent)} />
                      ))}
                    </Pie>
                    <Tooltip content={<ChartTooltip format={(v) => `${count(v)} (${pct(v / Math.max(intentTotal, 1))})`} />} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <Legend items={intents.map((i) => ({ label: `${i.intent.replaceAll("_", " ")} ${pct(i.count / Math.max(intentTotal, 1))}`, color: colorFor(i.intent) }))} />
            </div>
            <div className="h-44">
              <ResponsiveContainer>
                <BarChart data={tiers} margin={{ top: 8, right: 8 }}>
                  <CartesianGrid {...GRID} />
                  <XAxis dataKey="tier" {...AXIS} />
                  <YAxis {...AXIS} tickFormatter={count} width={40} />
                  <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip format={(v) => count(v)} />} />
                  <Bar dataKey="count" name="Requests" {...BAR} isAnimationActive={false}>
                    {tiers.map((t) => (
                      <Cell key={t.tier} fill={colorFor(t.tier)} />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </ChartCard>

        <ChartCard
          {...common}
          title={topProvider ? `${topProvider.provider} served ${pct(topProvider.requests / provTotal)} of model calls; ${count(fallbacks)} fallbacks` : "Model providers"}
          subtitle="Model calls by provider, and fallbacks to the next provider over time"
          howTo="Providers are tried in a fixed order; when one fails or is rate-limited the call falls back to the next. The line counts fallback events per interval."
          rows={[...providers.map((p) => ({ provider: p.provider, requests: p.requests })), ...o.providers.fallbacks.map((f) => ({ fallback: `${f.from} → ${f.to}`, count: f.count }))]}
          raw={o.providers}
          filename="providers"
          empty={<EmptyState title="No model calls in this range" />}
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <div className="h-44">
                <ResponsiveContainer>
                  <PieChart>
                    <Pie data={providers} dataKey="requests" nameKey="provider" innerRadius="60%" outerRadius="90%" stroke="var(--surface)" strokeWidth={2} isAnimationActive={false}>
                      {providers.map((p) => (
                        <Cell key={p.provider} fill={colorFor(p.provider)} />
                      ))}
                    </Pie>
                    <Tooltip content={<ChartTooltip format={(v) => count(v)} />} />
                  </PieChart>
                </ResponsiveContainer>
              </div>
              <Legend items={providers.map((p) => ({ label: `${p.provider} ${pct(p.requests / Math.max(provTotal, 1))}`, color: colorFor(p.provider) }))} />
            </div>
            {o.providers.fallback_series.length === 0 ? (
              <p className="flex h-44 items-center justify-center text-center text-sm text-ink-2">No fallbacks recorded in this range.</p>
            ) : (
            <div className="h-44">
              <ResponsiveContainer>
                <LineChart data={o.providers.fallback_series.map(([t, v]) => ({ t, v }))} margin={{ top: 8, right: 8 }}>
                  <CartesianGrid {...GRID} />
                  <XAxis dataKey="t" {...AXIS} tickFormatter={tick} minTickGap={32} />
                  <YAxis {...AXIS} allowDecimals={false} width={32} />
                  <Tooltip content={<ChartTooltip format={(v) => count(v)} labelFormat={(l) => tick(Number(l))} />} />
                  <Line dataKey="v" name="Fallbacks" stroke="var(--series-8)" {...LINE} isAnimationActive={false} />
                </LineChart>
              </ResponsiveContainer>
            </div>
            )}
          </div>
        </ChartCard>

        <ChartCard
          {...common}
          title={
            shadow <= 0
              ? "Model cost over time"
              : saved >= 0
                ? `Caching and routing saved ${pct(saved)} of model cost`
                : `Actual cost ran ${pct(-saved)} above the no-cache estimate`
          }
          subtitle="Actual cost vs. what the same traffic would cost on the expensive tier without cache, USD"
          howTo="Shadow cost prices every answered request as if it had gone to the expensive model with no cache. The gap between the two lines is the saving."
          rows={cost.map((r) => ({ time: new Date(r.t * 1000).toISOString(), actual_usd: r.actual ?? null, shadow_usd: r.shadow ?? null }))}
          raw={o.cost_series}
          filename="cost"
          empty={<EmptyState title="No model cost in this range" />}
        >
          <div className="h-56">
            <ResponsiveContainer>
              <AreaChart data={cost} margin={{ top: 8, right: 8 }}>
                <CartesianGrid {...GRID} />
                <XAxis dataKey="t" {...AXIS} tickFormatter={tick} minTickGap={32} />
                <YAxis {...AXIS} tickFormatter={(v: number) => `$${v.toFixed(v < 1 ? 3 : 0)}`} width={56} />
                <Tooltip content={<ChartTooltip format={(v) => `$${v.toFixed(4)}`} labelFormat={(l) => tick(Number(l))} />} />
                <Area dataKey="shadow" name="Shadow" stroke={colorFor("shadow")} fill={colorFor("shadow")} fillOpacity={0.1} strokeWidth={2} isAnimationActive={false} />
                <Area dataKey="actual" name="Actual" stroke={colorFor("actual")} fill={colorFor("actual")} fillOpacity={0.1} strokeWidth={2} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
          <Legend shape="line" items={[{ label: `Actual ${money(actual)}`, color: colorFor("actual") }, { label: `Shadow ${money(shadow)}`, color: colorFor("shadow") }]} />
        </ChartCard>

        <ChartCard
          {...common}
          title={lookups ? `Cache served ${pct(hits / lookups)} of answer lookups` : "Answer cache hits by layer"}
          subtitle="Answer-cache hits per interval, stacked by layer, count"
          howTo="L1 is in-process memory, L2 is the shared store, semantic matches a close paraphrase from the same permission scope. Misses are not drawn; the title compares hits with all lookups."
          rows={cache.map((r) => ({ time: new Date(r.t * 1000).toISOString(), l1: r.l1 ?? 0, l2: r.l2 ?? 0, semantic: r.semantic ?? 0 }))}
          raw={o.cache_series}
          filename="cache-layers"
          empty={<EmptyState title="No cache lookups in this range" />}
        >
          <div className="h-56">
            <ResponsiveContainer>
              {/* Stacked bars per interval, not areas: hits are discrete counts and an area would
                  interpolate across empty intervals (and draws nothing for a single sample). */}
              <BarChart data={cache} margin={{ top: 8, right: 8 }} barCategoryGap={1}>
                <CartesianGrid {...GRID} />
                <XAxis dataKey="t" {...AXIS} tickFormatter={tick} minTickGap={32} />
                <YAxis {...AXIS} tickFormatter={count} width={40} allowDecimals={false} />
                <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip format={(v) => count(v)} labelFormat={(l) => tick(Number(l))} />} />
                {(["l1", "l2", "semantic"] as const).map((k, i, all) => (
                  <Bar
                    key={k}
                    dataKey={k}
                    name={k === "semantic" ? "Semantic" : k.toUpperCase()}
                    stackId="hits"
                    fill={colorFor(k)}
                    stroke="var(--surface)"
                    strokeWidth={1}
                    maxBarSize={24}
                    radius={i === all.length - 1 ? [4, 4, 0, 0] : 0}
                    isAnimationActive={false}
                  />
                ))}
              </BarChart>
            </ResponsiveContainer>
          </div>
          <Legend items={[{ label: "L1", color: colorFor("l1") }, { label: "L2", color: colorFor("l2") }, { label: "Semantic", color: colorFor("semantic") }]} />
        </ChartCard>

        <ChartCard
          {...common}
          title={keptTotal ? `${pct(1 - keptZero / keptTotal)} of retrievals kept at least one passage` : "Passages kept by the relevance check"}
          subtitle="Left: passages kept per request (count of requests). Right: relevance scores of all checked passages"
          howTo="After retrieval, each of the top passages is scored for relevance and injection risk; only relevant, safe ones reach the model. A request that keeps 0 passages gets the no-answer message."
          rows={[...o.gate_kept.map((b) => ({ histogram: "kept", le: b.le, count: b.count })), ...o.gate_relevance.map((b) => ({ histogram: "relevance", le: b.le, count: b.count }))]}
          raw={{ gate_kept: o.gate_kept, gate_relevance: o.gate_relevance }}
          filename="gate"
          empty={<EmptyState title="No gated requests in this range" />}
        >
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div className="h-48">
              <ResponsiveContainer>
                <BarChart data={kept} margin={{ top: 8, right: 8 }}>
                  <CartesianGrid {...GRID} />
                  <XAxis dataKey="bucket" {...AXIS} />
                  <YAxis {...AXIS} tickFormatter={count} width={40} />
                  <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip format={(v) => `${count(v)} requests`} />} />
                  <Bar dataKey="count" name="Requests" fill="var(--series-1)" {...BAR} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </div>
            <div className="h-48">
              <ResponsiveContainer>
                <BarChart data={rel} margin={{ top: 8, right: 8 }} barCategoryGap={2}>
                  <CartesianGrid {...GRID} />
                  <XAxis dataKey="bucket" {...AXIS} interval="preserveStartEnd" />
                  <YAxis {...AXIS} tickFormatter={count} width={40} />
                  <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip format={(v) => `${count(v)} passages`} />} />
                  <Bar dataKey="count" name="Passages" fill="var(--series-1)" {...BAR} isAnimationActive={false} />
                </BarChart>
              </ResponsiveContainer>
            </div>
          </div>
        </ChartCard>

        <ChartCard
          {...common}
          title={blockTotal ? `${blocks[0].layer} caught the most attempts (${pct(blocks[0].count / blockTotal)} of ${count(blockTotal)} blocks)` : "No guardrail blocks in this range"}
          subtitle="Requests blocked, by the guard layer that stopped them, count"
          howTo="regex: known attack phrasings; jev: the router's injection score; classifier: the local injection model; ensemble: two weaker signals agreeing; documents: every relevant passage looked injected; output: the answer leaked protected text."
          rows={blocks.map((b) => ({ layer: b.layer, count: b.count }))}
          raw={o.guard_blocks}
          filename="guard-blocks"
          empty={<EmptyState title="Nothing blocked in this range" />}
        >
          <div style={{ height: blocks.length * 34 + 30 }}>
            <ResponsiveContainer>
              <BarChart data={blocks} layout="vertical" margin={{ left: 8, right: 40 }}>
                <CartesianGrid {...GRID} horizontal={false} vertical />
                <XAxis type="number" {...AXIS} allowDecimals={false} />
                <YAxis type="category" dataKey="layer" {...AXIS} width={120} />
                <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip format={(v) => count(v)} />} />
                <Bar dataKey="count" name="Blocks" fill="var(--series-1)" {...BAR_H} isAnimationActive={false} label={{ position: "right", fill: "var(--ink-2)", fontSize: 12 }} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        </ChartCard>
      </div>
    </div>
  );
}
