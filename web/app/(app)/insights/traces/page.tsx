"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { Route, Zap } from "lucide-react";
import { Badge, type Tone } from "@/components/ui/badge";
import { Card, PageHeader } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { get } from "@/lib/api";
import { dateTime, ms } from "@/lib/format";
import type { TraceRow } from "@/lib/types";

// Outcome colour: answered = green, no answer = amber (the assistant held back), blocked/error = red.
const outcomeTone = (s: string): Tone => (s === "no_answer" ? "warn" : /block|error|denied|fail/.test(s) ? "crit" : /answer|ok|done|executed/.test(s) ? "good" : "neutral");
// Slow is a symptom worth seeing: >= 3 s amber, >= 10 s red.
const latencyClass = (v: number) => (v >= 10_000 ? "font-semibold text-danger-text" : v >= 3_000 ? "font-medium text-warn-text" : "");

export default function TracesPage() {
  const q = useQuery({ queryKey: ["traces"], queryFn: () => get<{ items: TraceRow[] }>("/admin/traces?limit=50"), refetchInterval: 15_000 });
  return (
    <>
      <PageHeader icon={Route} tone="violet" title="Traces" description="The 50 most recent chat requests. Open one for its stage-by-stage timing." />
      {q.isPending ? (
        <Skeleton className="h-80" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.data.items.length === 0 ? (
        <EmptyState title="No requests recorded since the gateway started">Ask the assistant something, then come back.</EmptyState>
      ) : (
        <Card className="overflow-x-auto">
          <table className="w-full text-sm">
            <caption className="sr-only">Recent requests</caption>
            <thead className="text-left text-xs text-ink-2">
              <tr className="border-b border-line">
                {["Time", "Request", "Role", "Outcome", "Route", "Cache", "Total"].map((h) => (
                  <th key={h} scope="col" className={`px-3 py-2 font-medium ${h === "Total" ? "text-right" : ""}`}>
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody className="tabular">
              {q.data.items.map((t) => (
                <tr key={t.request_id} className="border-b border-line last:border-0 hover:bg-surface-2">
                  <td className="px-3 py-2 whitespace-nowrap text-ink-2">{dateTime(t.at)}</td>
                  <td className="px-3 py-2">
                    <Link href={`/insights/traces/${t.request_id}`} className="font-mono text-xs underline-offset-4 hover:underline">
                      {t.request_id.slice(0, 8)}
                    </Link>
                  </td>
                  <td className="px-3 py-2">{t.role}</td>
                  <td className="px-3 py-2">
                    <Badge tone={outcomeTone(t.status)}>{t.status.replaceAll("_", " ")}</Badge>
                    {t.reason ? <span className="ml-2 text-ink-2">{t.reason}</span> : null}
                  </td>
                  <td className="px-3 py-2 text-ink-2">{[t.intent, t.tier].filter(Boolean).join(" · ") || "—"}</td>
                  <td className="px-3 py-2">{t.cache_layer ? <Badge tone="info" icon={Zap}>{t.cache_layer}</Badge> : <span className="text-ink-2">—</span>}</td>
                  <td className={`px-3 py-2 text-right ${latencyClass(t.total_ms)}`}>{ms(t.total_ms)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
