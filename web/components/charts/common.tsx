"use client";

import type { ReactNode } from "react";

// Shared Recharts props so every chart has the same recessive chrome (hairline grid, muted ticks).
export const GRID = { stroke: "var(--grid)", strokeDasharray: undefined, vertical: false } as const;
export const AXIS = {
  stroke: "var(--axis)",
  tick: { fill: "var(--ink-2)", fontSize: 12 },
  tickLine: false,
} as const;
export const BAR = { radius: [4, 4, 0, 0] as [number, number, number, number], maxBarSize: 24 };
export const BAR_H = { radius: [0, 4, 4, 0] as [number, number, number, number], maxBarSize: 24 };
export const LINE = { strokeWidth: 2, dot: false, activeDot: { r: 4, stroke: "var(--surface)", strokeWidth: 2 } };

export interface TooltipEntry {
  name?: string | number;
  value?: number | string | (number | string)[];
  color?: string;
  dataKey?: string | number;
  payload?: Record<string, unknown>;
}

/** Values lead, labels follow; each row keyed by a short line in the series colour. */
export function ChartTooltip({
  active,
  payload,
  label,
  format,
  labelFormat,
}: {
  active?: boolean;
  payload?: TooltipEntry[];
  label?: ReactNode;
  format: (v: number, name: string) => string;
  labelFormat?: (l: unknown) => string;
}) {
  if (!active || !payload?.length) return null;
  return (
    <div className="rounded-md border border-line bg-surface px-3 py-2 text-xs shadow-lg">
      {label !== undefined && <p className="mb-1 text-ink-2">{labelFormat ? labelFormat(label) : String(label)}</p>}
      <ul className="space-y-0.5">
        {payload.map((p, i) => {
          // A formatter may return "" to hide a helper series (e.g. a CI band) from the readout.
          const text = format(Number(Array.isArray(p.value) ? p.value[1] : p.value), String(p.name ?? ""));
          return text === "" ? null : (
            <li key={i} className="flex items-center gap-2">
              <span aria-hidden className="h-0.5 w-3 rounded" style={{ background: p.color }} />
              <span className="font-semibold text-ink">{text}</span>
              <span className="text-ink-2">{p.name}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}

export function Legend({ items, shape = "rect" }: { items: { label: string; color: string }[]; shape?: "rect" | "line" }) {
  if (items.length < 2) return null; // one series: the title names it
  return (
    <ul className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-ink-2">
      {items.map((i) => (
        <li key={i.label} className="inline-flex items-center gap-1.5">
          <span aria-hidden className={shape === "line" ? "h-0.5 w-3.5 rounded" : "size-2.5 rounded-sm"} style={{ background: i.color }} />
          {i.label}
        </li>
      ))}
    </ul>
  );
}
