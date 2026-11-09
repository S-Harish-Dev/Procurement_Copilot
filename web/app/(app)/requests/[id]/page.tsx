"use client";

import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, Circle, Clock, Download, XCircle } from "lucide-react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { Card, PageHeader } from "@/components/ui/card";
import { ErrorState, Skeleton } from "@/components/ui/states";
import { StatusPill } from "@/components/ui/status";
import { useToast } from "@/components/ui/toast";
import { download, tool } from "@/lib/api";
import { dateTime, levelLabel, money, statusLabel } from "@/lib/format";
import type { ChainStep, PurchaseRequest } from "@/lib/types";

const STEP_ICON = { approved: CheckCircle2, rejected: XCircle, waiting: Clock, upcoming: Circle };
const STEP_COLOR = { approved: "var(--status-good)", rejected: "var(--status-critical)", waiting: "var(--status-warning)", upcoming: "var(--other)" };
const STEP_TEXT = { approved: "Approved", rejected: "Rejected", waiting: "Waiting", upcoming: "Not yet" };

export default function RequestDetail() {
  const { id } = useParams<{ id: string }>();
  const toast = useToast();
  const [downloading, setDownloading] = useState(false);
  const pr = useQuery({ queryKey: ["pr", id], queryFn: () => tool<PurchaseRequest>("get_purchase_request", { pr_id: id }) });
  const chain = useQuery({ queryKey: ["chain", id], queryFn: () => tool<{ steps: ChainStep[] }>("get_approval_chain", { pr_id: id }) });

  if (pr.isPending) return <Skeleton className="h-96" />;
  if (pr.isError) return <ErrorState error={pr.error} onRetry={() => pr.refetch()} />;
  const r = pr.data;
  const hasPo = r.status === "approved" || r.status === "ordered";

  // Timeline from what the record holds: creation, each decision, and the current state.
  const timeline = [
    { at: r.created_at, title: "Request raised", detail: `by ${r.requester_name}` },
    ...r.approvals.map((a) => ({
      at: a.decided_at,
      title: `${levelLabel(a.level)} ${a.decision}`,
      detail: `${a.approver}${a.comment ? ` — “${a.comment}”` : ""}`,
    })),
    { at: r.updated_at, title: `Now: ${statusLabel(r.status)}`, detail: "" },
  ];

  async function getPo() {
    setDownloading(true);
    try {
      await download(`/v1/files/po/${encodeURIComponent(r.id)}`, `${r.id}-PO.pdf`);
    } catch (e) {
      toast({ title: "Purchase order not available", body: e instanceof Error ? e.message : undefined, tone: "error" });
    } finally {
      setDownloading(false);
    }
  }

  return (
    <>
      <p className="mb-2 text-sm">
        <Link href="/requests" className="text-ink-2 underline-offset-4 hover:text-ink hover:underline">
          ← My requests
        </Link>
      </p>
      <PageHeader
        title={r.id}
        description={`${r.vendor_name} · ${r.department_id} · raised ${dateTime(r.created_at)}`}
        actions={
          hasPo ? (
            <Button variant="primary" onClick={getPo} disabled={downloading}>
              <Download className="size-4" aria-hidden /> Purchase order (PDF)
            </Button>
          ) : undefined
        }
      />
      <div className="grid gap-4 lg:grid-cols-3">
        <Card className="p-5 lg:col-span-2">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <StatusPill status={r.status} />
            <span className="text-2xl font-semibold">{money(r.total)}</span>
          </div>
          {r.justification && <p className="mt-3 text-sm text-ink-2">Why: {r.justification}</p>}
          <table className="mt-4 w-full text-sm">
            <caption className="sr-only">Line items</caption>
            <thead className="text-left text-xs text-ink-2">
              <tr className="border-b border-line">
                <th scope="col" className="py-2 font-medium">Item</th>
                <th scope="col" className="py-2 text-right font-medium">Qty</th>
                <th scope="col" className="py-2 text-right font-medium">Unit price</th>
                <th scope="col" className="py-2 text-right font-medium">Amount</th>
              </tr>
            </thead>
            <tbody className="tabular">
              {r.line_items.map((li) => (
                <tr key={li.line_no} className="border-b border-line last:border-0">
                  <td className="py-2">
                    {li.description} <span className="text-ink-2">({li.sku})</span>
                  </td>
                  <td className="py-2 text-right">{li.qty}</td>
                  <td className="py-2 text-right">{money(li.unit_price)}</td>
                  <td className="py-2 text-right">{money(li.qty * li.unit_price)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Card>

        <Card className="p-5">
          <h2 className="font-semibold">Approval progress</h2>
          {chain.isPending ? (
            <Skeleton className="mt-3 h-24" />
          ) : chain.isError ? (
            <p className="mt-3 text-sm text-ink-2">Couldn’t load the approval chain.</p>
          ) : chain.data.steps.length === 0 ? (
            <p className="mt-3 text-sm text-ink-2">No approval needed: approved automatically for this amount.</p>
          ) : (
            <ol className="mt-3 space-y-3" aria-label="Approval steps">
              {chain.data.steps.map((s) => {
                const Icon = STEP_ICON[s.state];
                return (
                  <li key={s.level} className="flex items-start gap-2.5">
                    <Icon aria-hidden className="mt-0.5 size-5 shrink-0" style={{ color: STEP_COLOR[s.state] }} />
                    <div>
                      <p className="text-sm font-medium">{levelLabel(s.level)}</p>
                      <p className="text-xs text-ink-2">
                        {STEP_TEXT[s.state]}
                        {s.by ? ` · ${s.by}` : ""}
                      </p>
                    </div>
                  </li>
                );
              })}
            </ol>
          )}
          <h2 className="mt-6 font-semibold">Timeline</h2>
          <ol className="mt-3 space-y-3 border-l border-line pl-4">
            {timeline.map((t, i) => (
              <li key={i} className="relative">
                <span aria-hidden className="absolute top-1.5 -left-[21px] size-2.5 rounded-full border-2 border-surface" style={{ background: "var(--series-1)" }} />
                <p className="text-sm font-medium capitalize">{t.title}</p>
                <p className="text-xs text-ink-2">
                  {dateTime(t.at)}
                  {t.detail ? ` · ${t.detail}` : ""}
                </p>
              </li>
            ))}
          </ol>
        </Card>
      </div>
    </>
  );
}
