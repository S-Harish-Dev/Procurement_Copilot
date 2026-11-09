"use client";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { AlarmClock, AlertTriangle, CircleDollarSign, ClipboardCheck, Clock, Sparkles, type LucideIcon } from "lucide-react";
import { useState } from "react";
import { ActionCard } from "@/components/chat/action-card";
import { BudgetBar } from "@/components/charts/budget-bar";
import { Badge, TONE, type Tone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, PageHeader } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { StatusPill } from "@/components/ui/status";
import { useToast } from "@/components/ui/toast";
import { cancelAction, confirmAction } from "@/lib/actions";
import { tool } from "@/lib/api";
import type { ActionOutcome } from "@/lib/conversations";
import { date, money } from "@/lib/format";
import { useSession } from "@/lib/use-session";
import type { Budget, Items, PendingRow, Proposal } from "@/lib/types";

// Design thresholds, not policy: how long a request has waited sets how loudly it asks for attention.
const OVERDUE_DAYS = 5;
const AGING_DAYS = 2;

type Urgency = { key: "overdue" | "aging" | "new"; tone: Tone; label: string; icon: LucideIcon };
const URGENCY: Record<Urgency["key"], Urgency> = {
  overdue: { key: "overdue", tone: "crit", label: "Overdue", icon: AlarmClock },
  aging: { key: "aging", tone: "warn", label: "Aging", icon: Clock },
  new: { key: "new", tone: "info", label: "New", icon: Sparkles },
};
const urgencyOf = (days: number): Urgency => URGENCY[days >= OVERDUE_DAYS ? "overdue" : days >= AGING_DAYS ? "aging" : "new"];
const ageDaysOf = (createdAt: string, now: number) => Math.floor((now - new Date(createdAt).getTime()) / 86_400_000);

/** Amount against the approver's personal limit: a meter whose track is a lighter step of the same ramp. */
function LimitMeter({ amount, limit }: { amount: number; limit: number }) {
  const ratio = limit > 0 ? amount / limit : 1;
  const over = ratio > 1;
  const near = !over && ratio >= 0.8;
  return (
    <div>
      <div className="flex items-baseline justify-between text-xs text-ink-2">
        <span>Of your approval limit ({money(limit)})</span>
        <span className="font-medium text-ink">{Math.round(ratio * 100)}%</span>
      </div>
      <div className="mt-1 h-2 w-full overflow-hidden rounded" style={{ background: "var(--surface-2)" }} role="img" aria-label={`${Math.round(ratio * 100)}% of your approval limit`}>
        <div className="h-full rounded" style={{ width: `${Math.min(ratio, 1) * 100}%`, background: over ? "var(--status-serious)" : near ? "var(--status-warning)" : "var(--seq-400)" }} />
      </div>
      {over && (
        <p className="mt-1.5 inline-flex items-center gap-1.5 rounded-md bg-serious-soft px-2 py-1 text-xs text-serious-text">
          <AlertTriangle aria-hidden className="size-3.5" />
          Above your limit: approving routes it on to the next level.
        </p>
      )}
    </div>
  );
}

function ApprovalCard({ row, limit }: { row: PendingRow; limit: number }) {
  const toast = useToast();
  const qc = useQueryClient();
  const [comment, setComment] = useState("");
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [outcome, setOutcome] = useState<ActionOutcome | undefined>();
  const [busy, setBusy] = useState(false);
  const budget = useQuery({
    queryKey: ["budget", row.department_id],
    queryFn: () => tool<Budget>("get_budget", { department: row.department_id }),
    retry: false,
  });

  async function propose(decision: "approve_request" | "reject_request") {
    setBusy(true);
    try {
      const p = await tool<Proposal>(decision, { pr_id: row.id, comment: comment || (decision === "approve_request" ? "Approved" : "Rejected") });
      setProposal(p);
      setOutcome(undefined);
    } catch (e) {
      toast({ title: "Not allowed", body: e instanceof Error ? e.message : undefined, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  // Age is measured once per mount; render stays pure and the number doesn't tick mid-read.
  const [mountedAt] = useState(() => Date.now());
  const ageDays = ageDaysOf(row.created_at, mountedAt);
  const urgency = urgencyOf(ageDays);
  const commentId = `comment-${row.id}`;

  return (
    <Card className={`p-4 ${urgency.key === "overdue" ? "border-critical/40" : urgency.key === "aging" ? "border-warning/50" : ""}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <p className="flex flex-wrap items-center gap-2 font-semibold">
            {row.id} · {row.vendor}
            <Badge tone={urgency.tone} icon={urgency.icon}>
              {urgency.label}
            </Badge>
          </p>
          <p className="mt-0.5 text-sm text-ink-2">
            {row.requester} · {row.department_id} · waiting{" "}
            <span className={`font-medium ${urgency.key === "new" ? "" : urgency.key === "overdue" ? "text-danger-text" : "text-warn-text"}`}>
              {ageDays} {ageDays === 1 ? "day" : "days"}
            </span>{" "}
            (since {date(row.created_at)})
          </p>
        </div>
        <div className="flex flex-col items-end gap-1">
          <p className="tabular text-xl font-semibold">{money(row.total)}</p>
          <StatusPill status={row.status} />
        </div>
      </div>
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <LimitMeter amount={row.total} limit={limit} />
        <div>
          <p className="mb-1 text-xs text-ink-2">{row.department_id} budget this year</p>
          {budget.data ? <BudgetBar budget={budget.data} /> : budget.isPending ? <Skeleton className="h-6" /> : <p className="text-xs text-ink-2">Budget not visible to you.</p>}
        </div>
      </div>
      {!proposal ? (
        <div className="mt-4 flex flex-wrap items-end gap-2">
          <div className="min-w-48 flex-1">
            <label htmlFor={commentId} className="text-xs text-ink-2">
              Comment (optional)
            </label>
            <input id={commentId} value={comment} onChange={(e) => setComment(e.target.value)} className="mt-1 h-10 w-full rounded-lg border border-line bg-page px-3 text-sm focus-visible:border-btn" />
          </div>
          <Button variant="success" onClick={() => propose("approve_request")} disabled={busy}>
            Approve
          </Button>
          <Button variant="danger" onClick={() => propose("reject_request")} disabled={busy}>
            Reject
          </Button>
        </div>
      ) : (
        <ActionCard
          proposal={proposal}
          outcome={outcome}
          onConfirm={async () => {
            const o = await confirmAction(proposal);
            setOutcome(o);
            toast({ title: o.status === "executed" ? "Decision recorded" : "Not completed", body: o.message, tone: o.status === "executed" ? "success" : "error" });
            if (o.status === "executed") void qc.invalidateQueries({ queryKey: ["pending"] });
          }}
          onCancel={async () => {
            await cancelAction(proposal);
            setProposal(null);
          }}
        />
      )}
    </Card>
  );
}

/** At-a-glance triage: how many are overdue / aging / new, and the money waiting on you. */
function Summary({ rows }: { rows: PendingRow[] }) {
  const [now] = useState(() => Date.now());
  const n = { overdue: 0, aging: 0, new: 0 };
  for (const r of rows) n[urgencyOf(ageDaysOf(r.created_at, now)).key]++;
  const total = rows.reduce((a, r) => a + r.total, 0);
  const tiles: { label: string; value: string | number; tone: Tone; icon: LucideIcon }[] = [
    { label: "Overdue", value: n.overdue, tone: n.overdue ? "crit" : "neutral", icon: AlarmClock },
    { label: "Aging", value: n.aging, tone: n.aging ? "warn" : "neutral", icon: Clock },
    { label: "New", value: n.new, tone: n.new ? "info" : "neutral", icon: Sparkles },
    { label: "Total waiting", value: money(total), tone: "neutral", icon: CircleDollarSign },
  ];
  return (
    <ul className="mb-4 grid grid-cols-2 gap-3 lg:grid-cols-4">
      {tiles.map((t) => (
        <li key={t.label} className="flex items-center gap-3 rounded-xl border border-line bg-surface p-3 shadow-[0_1px_2px_rgba(11,11,11,0.05)]">
          <span aria-hidden className={`inline-flex size-10 shrink-0 items-center justify-center rounded-lg ${TONE[t.tone]}`}>
            <t.icon className="size-5" />
          </span>
          <span>
            <span className="tabular block text-xl leading-tight font-semibold">{t.value}</span>
            <span className="block text-xs text-ink-2">{t.label}</span>
          </span>
        </li>
      ))}
    </ul>
  );
}

export default function ApprovalsPage() {
  const session = useSession()!;
  const q = useQuery({ queryKey: ["pending"], queryFn: () => tool<Items<PendingRow>>("list_pending_approvals") });
  // Most urgent first: the longest-waiting requests, then the largest.
  const rows = [...(q.data?.items ?? [])].sort((a, b) => a.created_at.localeCompare(b.created_at) || b.total - a.total);
  return (
    <>
      <PageHeader icon={ClipboardCheck} tone="warn" title="Approvals" description="Requests waiting for your decision, longest-waiting first. Every decision asks you to confirm before it is recorded." />
      {q.isPending ? (
        <div className="space-y-3">
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
        </div>
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : rows.length === 0 ? (
        <EmptyState title="Nothing waiting for you">New requests that need your approval will appear here.</EmptyState>
      ) : (
        <div className="space-y-3">
          <Summary rows={rows} />
          {rows.map((r) => (
            <ApprovalCard key={r.id} row={r} limit={session.user.approval_limit} />
          ))}
        </div>
      )}
    </>
  );
}
