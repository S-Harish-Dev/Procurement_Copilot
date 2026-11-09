"use client";

import Link from "next/link";
import { Check, CircleCheck, Loader2 } from "lucide-react";
import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { levelLabel, money } from "@/lib/format";
import type { ActionOutcome } from "@/lib/conversations";
import type { Proposal } from "@/lib/types";

const TITLES: Record<string, string> = {
  create_purchase_request: "Create purchase request",
  submit_purchase_request: "Submit purchase request",
  approve_request: "Approve request",
  reject_request: "Reject request",
};

/** Who signs, in order. `current` marks the level this action acts at. */
export function ApprovalStepper({ chain, current }: { chain: string[]; current?: string }) {
  if (chain.length === 0) {
    return <p className="text-sm text-ink-2">No approval needed: this amount is approved automatically.</p>;
  }
  const curIdx = current ? chain.indexOf(current) : -1;
  return (
    <ol className="flex flex-wrap items-center gap-2 text-sm" aria-label="Approval chain">
      {chain.map((level, i) => {
        const done = curIdx > i;
        const active = curIdx === i;
        return (
          <li key={level} className="flex items-center gap-2">
            {i > 0 && <span aria-hidden className="h-px w-5 bg-line" />}
            <span
              className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 ${active ? "border-btn bg-info-soft font-medium text-info-text" : done ? "border-good/40 bg-good-soft text-success-text" : "border-line text-ink-2"}`}
              aria-current={active ? "step" : undefined}
            >
              <span
                aria-hidden
                className="inline-flex size-5 items-center justify-center rounded-full text-xs font-semibold text-white"
                style={{ background: done ? "var(--success-text)" : active ? "var(--btn)" : "var(--muted)" }}
              >
                {done ? <Check className="size-3" /> : i + 1}
              </span>
              {levelLabel(level)}
              {active && <span className="sr-only">(this step)</span>}
            </span>
          </li>
        );
      })}
    </ol>
  );
}

/** Remaining budget before vs after: one bar, "left after" and "this request" with a 2px gap. */
export function BudgetImpact({ before, after }: { before: number; after: number }) {
  const spend = Math.max(before - after, 0);
  const keep = Math.max(after, 0);
  const total = Math.max(before, spend, 1);
  const over = after < 0;
  return (
    <div>
      <div className="flex items-baseline justify-between text-sm">
        <span className="text-ink-2">Department budget remaining</span>
        <span>
          {money(before)} → <span className={over ? "font-semibold text-danger-text" : "font-semibold"}>{money(after)}</span>
        </span>
      </div>
      <div
        className="mt-2 flex h-3 w-full gap-[2px] overflow-hidden rounded"
        role="img"
        aria-label={`Remaining budget goes from ${money(before)} to ${money(after)}; this request uses ${money(spend)}.`}
      >
        <div style={{ width: `${(keep / total) * 100}%`, background: "var(--seq-250)" }} />
        {/* At least 3px: a small request on a large budget must still be visible. */}
        <div style={{ width: `${(spend / total) * 100}%`, minWidth: spend > 0 ? 3 : 0, background: "var(--series-2)" }} />
      </div>
      <div className="mt-1.5 flex flex-wrap gap-4 text-xs text-ink-2">
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="size-2.5 rounded-sm" style={{ background: "var(--seq-250)" }} /> Left after this request
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span aria-hidden className="size-2.5 rounded-sm" style={{ background: "var(--series-2)" }} /> This request {money(spend)}
        </span>
      </div>
    </div>
  );
}

export function ActionCard({
  proposal,
  outcome,
  onConfirm,
  onCancel,
}: {
  proposal: Proposal;
  outcome?: ActionOutcome;
  onConfirm: () => Promise<void>;
  onCancel: () => Promise<void>;
}) {
  const [busy, setBusy] = useState<"confirm" | "cancel" | null>(null);
  const s = proposal.summary;
  const isCreate = proposal.action === "create_purchase_request";
  const items = Array.isArray(s.items) ? s.items : [];

  async function run(kind: "confirm" | "cancel") {
    setBusy(kind);
    try {
      await (kind === "confirm" ? onConfirm() : onCancel());
    } finally {
      setBusy(null);
    }
  }

  return (
    <section
      aria-label={TITLES[proposal.action] ?? "Proposed action"}
      className={`mt-3 rounded-xl border bg-surface p-4 shadow-[0_1px_2px_rgba(11,11,11,0.05)] ${outcome ? (outcome.status === "executed" ? "border-good/50" : "border-line") : "border-warning/60"}`}
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="flex flex-wrap items-center gap-2 font-semibold">
          {TITLES[proposal.action] ?? proposal.action}
          {outcome ? (
            <Badge tone={outcome.status === "executed" ? "good" : "neutral"}>{outcome.status === "executed" ? "Done" : "Not completed"}</Badge>
          ) : (
            <Badge tone="warn">Needs your confirmation</Badge>
          )}
        </h3>
        {typeof s.total === "number" && <span className="tabular text-lg font-semibold">{money(s.total)}</span>}
      </div>
      <p className="mt-0.5 text-sm text-ink-2">
        {s.vendor ? <>Vendor: <span className="text-ink">{String(s.vendor)}</span></> : null}
        {s.pr_id ? <> · Request <span className="text-ink">{String(s.pr_id)}</span></> : null}
        {s.requester ? <> · Requested by <span className="text-ink">{String(s.requester)}</span></> : null}
      </p>

      {items.length > 0 && (
        <table className="mt-3 w-full text-sm">
          <caption className="sr-only">Line items</caption>
          <thead className="text-left text-xs text-ink-2">
            <tr>
              <th scope="col" className="py-1 font-medium">Item</th>
              <th scope="col" className="py-1 text-right font-medium">Qty</th>
              <th scope="col" className="py-1 text-right font-medium">Unit price</th>
              <th scope="col" className="py-1 text-right font-medium">Amount</th>
            </tr>
          </thead>
          <tbody className="tabular">
            {items.map((it, i) => (
              <tr key={`${it.sku}-${i}`} className="border-t border-line">
                <td className="py-1.5">{it.desc || it.sku}</td>
                <td className="py-1.5 text-right">{it.qty}</td>
                <td className="py-1.5 text-right">{money(it.unit_price)}</td>
                <td className="py-1.5 text-right">{money(it.qty * it.unit_price)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {isCreate && typeof s.budget_remaining_before === "number" && typeof s.budget_remaining_after === "number" && (
        <div className="mt-4">
          <BudgetImpact before={s.budget_remaining_before} after={s.budget_remaining_after} />
        </div>
      )}

      <div className="mt-4">
        <p className="mb-2 text-xs font-medium text-ink-2">Who approves</p>
        <ApprovalStepper chain={Array.isArray(s.approval_chain) ? s.approval_chain : []} current={typeof s.level === "string" ? s.level : undefined} />
      </div>
      {s.comment ? <p className="mt-3 text-sm text-ink-2">Comment: “{String(s.comment)}”</p> : null}

      <div className="mt-4 flex flex-wrap items-center gap-2" aria-live="polite">
        {outcome ? (
          <p className={`inline-flex flex-wrap items-center gap-1.5 text-sm ${outcome.status === "executed" ? "text-success-text" : "text-ink-2"}`}>
            {outcome.status === "executed" && <CircleCheck className="size-4" style={{ color: "var(--status-good)" }} aria-hidden />}
            {outcome.message}
            {outcome.prId && (
              <Link href={`/requests/${outcome.prId}`} className="font-medium text-ink underline underline-offset-4">
                View {outcome.prId}
              </Link>
            )}
          </p>
        ) : (
          <>
            <Button variant="primary" onClick={() => run("confirm")} disabled={busy !== null}>
              {busy === "confirm" && <Loader2 className="size-4 animate-spin" aria-hidden />}
              Confirm
            </Button>
            <Button onClick={() => run("cancel")} disabled={busy !== null}>
              Cancel
            </Button>
            <span className="text-xs text-ink-2">Nothing happens until you confirm.</span>
          </>
        )}
      </div>
    </section>
  );
}
