"use client";

import { useQuery } from "@tanstack/react-query";
import { ExternalLink } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ChartCard } from "@/components/charts/chart-card";
import { PageHeader } from "@/components/ui/card";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { get } from "@/lib/api";
import { dateTime, ms } from "@/lib/format";
import type { TraceDetail } from "@/lib/types";

/** Waterfall: each span is a bar offset by its start time on one shared ms axis. */
function Waterfall({ t }: { t: TraceDetail }) {
  const end = Math.max(t.total_ms, ...t.spans.map((s) => s.start_ms + s.duration_ms), 1);
  const ticks = [0, 0.25, 0.5, 0.75, 1].map((f) => f * end);
  return (
    <div>
      <div className="ml-36 flex justify-between text-xs text-ink-2 tabular" aria-hidden>
        {ticks.map((v) => (
          <span key={v}>{ms(v)}</span>
        ))}
      </div>
      <ol className="mt-1 space-y-1.5">
        {t.spans.map((s, i) => (
          <li key={`${s.name}-${i}`} className="flex items-center gap-2 text-xs">
            <span className="w-34 shrink-0 truncate text-right font-mono" title={s.name}>
              {s.name}
            </span>
            <span className="relative h-4 flex-1 border-l border-line" style={{ backgroundImage: "linear-gradient(to right, var(--grid) 1px, transparent 1px)", backgroundSize: "25% 100%" }}>
              <span
                className="absolute top-0.5 h-3 rounded-sm"
                style={{ left: `${(s.start_ms / end) * 100}%`, width: `max(2px, ${(s.duration_ms / end) * 100}%)`, background: "var(--series-1)" }}
                title={`${s.name}: starts ${ms(s.start_ms)}, takes ${ms(s.duration_ms)}`}
              />
            </span>
            <span className="w-16 shrink-0 text-right tabular">{ms(s.duration_ms)}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

export default function TraceDetailPage() {
  const { id } = useParams<{ id: string }>();
  const q = useQuery({ queryKey: ["trace", id], queryFn: () => get<TraceDetail>(`/admin/traces/${encodeURIComponent(id)}`) });
  if (q.isPending) return <Skeleton className="h-80" />;
  if (q.isError) return <ErrorState error={q.error} onRetry={() => q.refetch()} />;
  const t = q.data;
  const slowest = [...t.spans].sort((a, b) => b.duration_ms - a.duration_ms)[0];
  return (
    <>
      <p className="mb-2 text-sm">
        <Link href="/insights/traces" className="text-ink-2 underline-offset-4 hover:text-ink hover:underline">
          ← Traces
        </Link>
      </p>
      <PageHeader
        title={`Request ${t.request_id.slice(0, 8)}`}
        description={`${dateTime(t.at)} · ${t.status} · ${ms(t.total_ms)} total${t.trace_id ? ` · trace ${t.trace_id}` : ""}`}
        actions={
          t.grafana_url ? (
            <a href={t.grafana_url} target="_blank" rel="noreferrer" className="inline-flex h-10 items-center gap-1.5 rounded-md border border-line px-4 text-sm hover:bg-surface-2">
              Open in Grafana <ExternalLink className="size-4" aria-hidden />
            </a>
          ) : undefined
        }
      />
      <div className="space-y-4">
        <ChartCard
          title={slowest ? `${slowest.name} took ${ms(slowest.duration_ms)} of ${ms(t.total_ms)}` : "Request timeline"}
          subtitle="Stage timing, milliseconds from the start of the request"
          howTo="Each bar starts when the stage started and is as long as it took. Overlapping bars ran in parallel (the router and the injection classifier)."
          source="gateway request log"
          updated={t.at}
          rows={t.spans.map((s) => ({ span: s.name, start_ms: s.start_ms, duration_ms: s.duration_ms }))}
          raw={t}
          filename={`trace-${t.request_id}`}
        >
          <Waterfall t={t} />
        </ChartCard>
        <details className="rounded-lg border border-line bg-surface p-4">
          <summary className="cursor-pointer font-medium">Diagnostics</summary>
          <pre className="mt-2 overflow-auto text-xs">{JSON.stringify(t.diagnostics, null, 2)}</pre>
        </details>
      </div>
    </>
  );
}
