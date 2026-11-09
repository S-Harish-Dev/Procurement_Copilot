"use client";

import { useQuery } from "@tanstack/react-query";
import Link from "next/link";
import { useState } from "react";
import { FileText } from "lucide-react";
import { TONE } from "@/components/ui/badge";
import { Card, PageHeader } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { STATUS, StatusPill } from "@/components/ui/status";
import { tool } from "@/lib/api";
import { date, money, statusLabel } from "@/lib/format";
import type { Items, RequestRow } from "@/lib/types";

const FILTERS = ["", "pending_manager", "pending_finance", "pending_admin", "approved", "ordered", "rejected", "cancelled"];

export default function RequestsPage() {
  const [status, setStatus] = useState("");
  const q = useQuery({
    queryKey: ["my-requests", status],
    queryFn: () => tool<Items<RequestRow>>("list_my_requests", status ? { status } : {}),
    placeholderData: (prev) => prev,
  });
  return (
    <>
      <PageHeader icon={FileText} title="My requests" description="Your most recent purchase requests (up to 20). Open one for its approval progress and purchase order." />
      {/* Filter chips carry the same colour as the status they select, so the filter doubles as a legend. */}
      <div role="radiogroup" aria-label="Filter by status" className="mb-4 flex flex-wrap gap-2">
        {FILTERS.map((f) => {
          const on = status === f;
          const Icon = f ? STATUS[f]?.icon : undefined;
          return (
            <button
              key={f}
              type="button"
              role="radio"
              aria-checked={on}
              onClick={() => setStatus(f)}
              className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-sm transition-colors ${on ? `border-transparent font-medium ${TONE[f ? (STATUS[f]?.tone ?? "neutral") : "info"]}` : "border-line text-ink-2 hover:bg-surface-2 hover:text-ink"}`}
            >
              {Icon && <Icon aria-hidden className="size-3.5" />}
              {f ? statusLabel(f) : "All"}
            </button>
          );
        })}
      </div>
      {q.isPending ? (
        <Skeleton className="h-64" />
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : q.data.items.length === 0 ? (
        <EmptyState title="No requests here">
          {status ? "Nothing with this status. Try another filter." : "Ask the assistant to raise a purchase request and it will show up here."}
        </EmptyState>
      ) : (
        <Card className={`overflow-x-auto ${q.isFetching ? "opacity-60" : ""}`}>
          <table className="w-full text-sm">
            <caption className="sr-only">My purchase requests</caption>
            <thead className="bg-surface-2/60 text-left text-xs text-ink-2">
              <tr className="border-b border-line">
                <th scope="col" className="px-4 py-2.5 font-medium">Request</th>
                <th scope="col" className="px-4 py-2.5 font-medium">Vendor</th>
                <th scope="col" className="px-4 py-2.5 text-right font-medium">Total</th>
                <th scope="col" className="px-4 py-2.5 font-medium">Status</th>
                <th scope="col" className="px-4 py-2.5 font-medium">Created</th>
              </tr>
            </thead>
            <tbody className="tabular">
              {q.data.items.map((r) => (
                <tr key={r.id} className="border-b border-line last:border-0 hover:bg-surface-2">
                  <td className="px-4 py-2.5">
                    <Link href={`/requests/${r.id}`} className="font-medium text-info-text underline-offset-4 hover:underline">
                      {r.id}
                    </Link>
                  </td>
                  <td className="px-4 py-2.5">{r.vendor}</td>
                  <td className="px-4 py-2.5 text-right font-medium">{money(r.total)}</td>
                  <td className="px-4 py-2.5">
                    <StatusPill status={r.status} />
                  </td>
                  <td className="px-4 py-2.5 text-ink-2">{date(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>
      )}
    </>
  );
}
