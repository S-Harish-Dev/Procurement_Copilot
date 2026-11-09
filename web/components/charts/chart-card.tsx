"use client";

import { Braces, Download, Table2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { InfoPopover } from "@/components/ui/info";
import { downloadText, toCsv } from "@/lib/csv";
import { dateTime } from "@/lib/format";

export type Row = Record<string, string | number | null>;

/**
 * Every chart's frame: a takeaway title, "how to read this", the data source and freshness, and three
 * ways to check the numbers (CSV, raw JSON, table view — the table is also the accessible twin).
 */
export function ChartCard({
  title,
  subtitle,
  howTo,
  source,
  updated,
  rows,
  raw,
  filename,
  children,
  className = "",
  empty,
}: {
  title: string;
  subtitle?: string;
  howTo: ReactNode;
  source: string;
  updated?: string;
  rows: Row[];
  raw: unknown;
  filename: string;
  children: ReactNode;
  className?: string;
  empty?: ReactNode;
}) {
  const [view, setView] = useState<"chart" | "table" | "json">("chart");
  const cols = rows.length ? Array.from(new Set(rows.flatMap((r) => Object.keys(r)))) : [];
  const toggle = (v: "table" | "json") => setView((cur) => (cur === v ? "chart" : v));
  return (
    <figure className={`flex min-w-0 flex-col rounded-xl border border-line bg-surface p-4 shadow-[0_1px_2px_rgba(11,11,11,0.05)] ${className}`}>
      {/* (i) is pinned to the top-right corner on every card, whatever the title length. */}
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0 flex-1">
          <figcaption className="leading-snug font-semibold text-balance">{title}</figcaption>
          {subtitle && <p className="mt-0.5 text-sm text-ink-2">{subtitle}</p>}
        </div>
        <span className="-mt-0.5 -mr-1 shrink-0">
          <InfoPopover label="How to read this chart">{howTo}</InfoPopover>
        </span>
      </div>
      {/* Flex column: a chart that flex-grows plus a legend below it always fit above the footer. */}
      <div className="mt-3 flex min-h-0 flex-1 flex-col">
        {rows.length === 0 && empty ? (
          empty
        ) : view === "chart" ? (
          children
        ) : view === "table" ? (
          <div className="max-h-72 overflow-auto">
            <table className="w-full text-xs">
              <caption className="sr-only">{title}: data table</caption>
              <thead className="sticky top-0 bg-surface text-left text-ink-2">
                <tr>
                  {cols.map((c) => (
                    <th key={c} scope="col" className="border-b border-line px-2 py-1.5 font-medium">
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody className="tabular">
                {rows.map((r, i) => (
                  <tr key={i} className="border-b border-line last:border-0">
                    {cols.map((c) => (
                      <td key={c} className="px-2 py-1">
                        {r[c] ?? ""}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <pre className="max-h-72 overflow-auto rounded bg-surface-2 p-2 text-xs">{JSON.stringify(raw, null, 2)}</pre>
        )}
      </div>
      <div className="mt-3 flex items-center justify-between gap-2 text-xs text-ink-2">
        <span className="min-w-0 truncate" title={`Source: ${source}${updated ? ` · updated ${dateTime(updated)}` : ""}`}>
          {source}
          {updated ? ` · ${dateTime(updated)}` : ""}
        </span>
        {/* Icon-only: the label lives in the tooltip so the footer stays one quiet line. */}
        <span className="flex shrink-0 gap-0.5">
          <button type="button" title="Table view" aria-label="Table view" onClick={() => toggle("table")} aria-pressed={view === "table"} className="rounded p-1.5 hover:bg-surface-2 hover:text-ink aria-pressed:bg-info-soft aria-pressed:text-info-text">
            <Table2 className="size-4" aria-hidden />
          </button>
          <button type="button" title="Raw JSON" aria-label="Raw JSON" onClick={() => toggle("json")} aria-pressed={view === "json"} className="rounded p-1.5 hover:bg-surface-2 hover:text-ink aria-pressed:bg-info-soft aria-pressed:text-info-text">
            <Braces className="size-4" aria-hidden />
          </button>
          <button
            type="button"
            title="Download CSV"
            aria-label="Download CSV"
            onClick={() => downloadText(toCsv(rows), `${filename}.csv`)}
            disabled={rows.length === 0}
            className="rounded p-1.5 hover:bg-surface-2 hover:text-ink disabled:opacity-50"
          >
            <Download className="size-4" aria-hidden />
          </button>
        </span>
      </div>
    </figure>
  );
}
