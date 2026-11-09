import { ArrowDownRight, ArrowUpRight, Minus } from "lucide-react";
import { Badge, type Tone } from "@/components/ui/badge";
import { formatUnit } from "@/lib/format";
import type { Kpi } from "@/lib/types";

// Whether "up" is good for each KPI, so the delta colour says better/worse, not just bigger/smaller.
const UP_IS_GOOD: Record<string, boolean> = {
  requests: true,
  cache_hit_rate: true,
  p95_latency_s: false,
  cost_actual_usd: false,
  cost_shadow_usd: false,
  guard_blocks: false,
  jev_degraded_ratio: false,
  abstention_rate: false,
};

const LINE: Record<string, string> = { good: "var(--status-good)", crit: "var(--status-critical)", neutral: "var(--other)" };

function Sparkline({ series, tone }: { series: [number, number][]; tone: Tone }) {
  if (series.length < 2) return null;
  const xs = series.map((p) => p[0]);
  const ys = series.map((p) => p[1]);
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  const w = 96;
  const h = 32;
  const px = (x: number) => ((x - x0) / Math.max(x1 - x0, 1)) * w;
  const py = (y: number) => h - 3 - ((y - y0) / Math.max(y1 - y0, 1e-9)) * (h - 6);
  const d = series.map((p, i) => `${i ? "L" : "M"}${px(p[0]).toFixed(1)},${py(p[1]).toFixed(1)}`).join("");
  const last = series[series.length - 1];
  const color = LINE[tone] ?? "var(--series-1)";
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} aria-hidden className="shrink-0 overflow-visible">
      <path d={d} fill="none" stroke={color} strokeOpacity={0.55} strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={px(last[0])} cy={py(last[1])} r={3} fill={color} stroke="var(--surface)" strokeWidth={1.5} />
    </svg>
  );
}

const SHELL = "rounded-xl border border-line bg-surface p-4 shadow-[0_1px_2px_rgba(11,11,11,0.05)]";

export function StatTile({ kpi }: { kpi: Kpi }) {
  if (kpi.value === null) {
    return (
      <div className={SHELL}>
        <p className="text-sm text-ink-2">{kpi.label}</p>
        <p className="mt-1 text-2xl font-semibold text-ink-2">No data</p>
        <p className="mt-1 text-xs text-ink-2">Nothing recorded in this range yet</p>
      </div>
    );
  }
  const value = <p className="tabular text-2xl font-semibold tracking-tight">{formatUnit(kpi.value, kpi.unit)}</p>;
  if (kpi.previous === null) {
    return (
      <div className={SHELL}>
        <p className="text-sm text-ink-2">{kpi.label}</p>
        <div className="mt-1 flex items-end justify-between gap-2">
          {value}
          <Sparkline series={kpi.series} tone="neutral" />
        </div>
        <p className="mt-2 text-xs text-ink-2">No previous period to compare</p>
      </div>
    );
  }
  const delta = kpi.value - kpi.previous;
  const rel = kpi.previous !== 0 ? delta / Math.abs(kpi.previous) : null;
  const flat = Math.abs(delta) < 1e-12;
  const good = flat ? null : (delta > 0) === (UP_IS_GOOD[kpi.id] ?? true);
  const tone: Tone = good === null ? "neutral" : good ? "good" : "crit";
  const Icon = flat ? Minus : delta > 0 ? ArrowUpRight : ArrowDownRight;
  return (
    <div className={SHELL}>
      <p className="text-sm text-ink-2">{kpi.label}</p>
      <div className="mt-1 flex items-end justify-between gap-2">
        {value}
        <Sparkline series={kpi.series} tone={tone} />
      </div>
      <p className="mt-2 flex items-center gap-2 text-xs text-ink-2">
        <Badge tone={tone} icon={Icon}>
          {flat ? "No change" : `${delta > 0 ? "+" : "−"}${rel === null ? formatUnit(Math.abs(delta), kpi.unit) : `${Math.abs(rel * 100).toFixed(0)}%`}`}
        </Badge>
        vs previous period
      </p>
    </div>
  );
}
