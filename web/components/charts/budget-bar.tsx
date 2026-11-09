import { money } from "@/lib/format";
import { colorFor } from "@/lib/palette";
import type { Budget } from "@/lib/types";

/** Part-to-whole of a department budget as one thin stacked bar (2px surface gaps), with a legend. */
export function BudgetBar({ budget, extra }: { budget: Budget; extra?: number }) {
  const parts = [
    { key: "spent", label: "Spent", value: Math.max(budget.spent, 0) },
    { key: "committed", label: "Committed", value: Math.max(budget.committed, 0) },
    { key: "remaining", label: "Remaining", value: Math.max(budget.remaining, 0) },
  ];
  const total = Math.max(budget.allocated, parts.reduce((a, p) => a + p.value, 0), 1);
  return (
    <div>
      <div
        className="flex h-2.5 w-full gap-[2px] overflow-hidden rounded"
        role="img"
        aria-label={`${budget.department} budget ${money(budget.allocated)}: spent ${money(budget.spent)}, committed ${money(budget.committed)}, remaining ${money(budget.remaining)}.`}
      >
        {parts.map((p) => (
          <div key={p.key} title={`${p.label}: ${money(p.value)}`} style={{ width: `${(p.value / total) * 100}%`, background: colorFor(p.key) }} />
        ))}
      </div>
      <ul className="mt-1.5 flex flex-wrap gap-x-3 gap-y-1 text-xs text-ink-2">
        {parts.map((p) => (
          <li key={p.key} className="inline-flex items-center gap-1.5">
            <span aria-hidden className="size-2.5 rounded-sm" style={{ background: colorFor(p.key) }} />
            {p.label} <span className="text-ink">{money(p.value)}</span>
          </li>
        ))}
        {extra !== undefined && <li className="text-ink">This request: {money(extra)}</li>}
      </ul>
    </div>
  );
}
