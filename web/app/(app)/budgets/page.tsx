"use client";

import { useQuery } from "@tanstack/react-query";
import { Wallet } from "lucide-react";
import { useState } from "react";
import { Bar, BarChart, CartesianGrid, Cell, Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { ChartCard } from "@/components/charts/chart-card";
import { AXIS, BAR, BAR_H, ChartTooltip, GRID, Legend } from "@/components/charts/common";
import { PageHeader } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { tool } from "@/lib/api";
import { money, moneyCompact, pct } from "@/lib/format";
import { colorFor } from "@/lib/palette";
import { useSession } from "@/lib/use-session";
import type { Budget, SpendSummary } from "@/lib/types";

const DEPARTMENTS = ["IT", "HR", "Legal", "Marketing", "Operations", "Finance"];
const SOURCE = "Acme procurement records";

/** Spend summary, or just the budget when the summary tool isn't available (charts then show empty states). */
async function loadSpend(department: string): Promise<SpendSummary> {
  try {
    return await tool<SpendSummary>("get_spend_summary", { department });
  } catch (e) {
    const b = await tool<Budget>("get_budget", { department }).catch(() => {
      throw e;
    });
    return { ...b, monthly: [], top_vendors: [] };
  }
}

/**
 * The summary skips months with no spend; a gap must read as a zero bar, not a missing month.
 * Fill from January of the fiscal year to the latest month (or the current month this year).
 */
function fillMonths(fy: number, monthly: SpendSummary["monthly"]): SpendSummary["monthly"] {
  const have = new Map(monthly.map((m) => [m.month, m.amount]));
  const latest = monthly.reduce((a, m) => (m.month > a ? m.month : a), `${fy}-01`);
  const [ly, lm] = latest.split("-").map(Number);
  const lastMonth = ly > fy ? 12 : lm;
  return Array.from({ length: Math.max(lastMonth, 1) }, (_, i) => {
    const key = `${fy}-${String(i + 1).padStart(2, "0")}`;
    return { month: key, amount: have.get(key) ?? 0 };
  }).concat(monthly.filter((m) => !m.month.startsWith(`${fy}-`)).sort((a, b) => a.month.localeCompare(b.month)));
}

const monthLabel = (m: string) => {
  const [y, mo] = m.split("-").map(Number);
  return new Date(y, (mo ?? 1) - 1, 1).toLocaleDateString("en-US", { month: "short", year: "2-digit" });
};

export default function BudgetsPage() {
  const user = useSession()!.user;
  const canSwitch = user.role === "finance" || user.role === "admin";
  const [dept, setDept] = useState(user.department);
  const q = useQuery({ queryKey: ["spend", dept], queryFn: () => loadSpend(dept), placeholderData: (p) => p });

  return (
    <>
      <PageHeader
        icon={Wallet}
        tone="good"
        title="Budgets"
        description={`Fiscal-year budget for ${dept}: what is spent, committed to approved requests, and still available.`}
        actions={
          canSwitch ? (
            <div className="flex items-center gap-2">
              <label htmlFor="dept" className="text-sm text-ink-2">
                Department
              </label>
              <select id="dept" value={dept} onChange={(e) => setDept(e.target.value)} className="h-10 rounded-xl border border-line bg-surface px-3 text-sm">
                {DEPARTMENTS.map((d) => (
                  <option key={d}>{d}</option>
                ))}
              </select>
            </div>
          ) : undefined
        }
      />
      {q.isPending ? (
        <div className="grid gap-4 lg:grid-cols-3">
          <Skeleton className="h-80" />
          <Skeleton className="h-80 lg:col-span-2" />
        </div>
      ) : q.isError ? (
        <ErrorState error={q.error} onRetry={() => q.refetch()} />
      ) : (
        <BudgetCharts s={q.data} stale={q.isFetching} />
      )}
    </>
  );
}

function BudgetCharts({ s, stale }: { s: SpendSummary; stale: boolean }) {
  const parts = [
    { key: "spent", label: "Spent", value: Math.max(s.spent, 0) },
    { key: "committed", label: "Committed", value: Math.max(s.committed, 0) },
    { key: "remaining", label: "Remaining", value: Math.max(s.remaining, 0) },
  ];
  const usedShare = s.allocated > 0 ? (s.spent + s.committed) / s.allocated : 0;
  const months = s.monthly.length ? fillMonths(s.fiscal_year, s.monthly) : [];
  const monthly = months.map((m) => ({ month: monthLabel(m.month), amount: m.amount }));
  const peak = months.reduce((a, m) => (m.amount > (a?.amount ?? -1) ? m : a), null as SpendSummary["monthly"][number] | null);
  const vendors = s.top_vendors.slice(0, 8);
  const topShare = vendors.length && s.spent > 0 ? vendors[0].amount / s.spent : 0;

  return (
    <div className={`grid gap-4 lg:grid-cols-3 ${stale ? "opacity-60 transition-opacity" : ""}`}>
      <ChartCard
        title={`${pct(usedShare)} of the ${s.department} budget is used or committed`}
        subtitle={`FY${s.fiscal_year} allocation ${money(s.allocated)}`}
        howTo="The ring is the whole annual allocation. Spent is paid or ordered; committed is approved but not yet ordered; remaining is still available for new requests."
        source={SOURCE}
        rows={parts.map((p) => ({ part: p.label, usd: p.value, share: s.allocated ? +(p.value / s.allocated).toFixed(4) : null }))}
        raw={s}
        filename={`budget-${s.department}`}
      >
        <div className="relative h-56">
          <ResponsiveContainer>
            <PieChart>
              <Pie data={parts} dataKey="value" nameKey="label" innerRadius="68%" outerRadius="92%" paddingAngle={1} stroke="var(--surface)" strokeWidth={2} isAnimationActive={false}>
                {parts.map((p) => (
                  <Cell key={p.key} fill={colorFor(p.key)} />
                ))}
              </Pie>
              <Tooltip content={<ChartTooltip format={(v) => money(v)} />} />
            </PieChart>
          </ResponsiveContainer>
          <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center">
            <span className="text-2xl font-semibold">{moneyCompact(s.remaining)}</span>
            <span className="text-xs text-ink-2">remaining</span>
          </div>
        </div>
        <Legend items={parts.map((p) => ({ label: `${p.label} ${moneyCompact(p.value)}`, color: colorFor(p.key) }))} />
      </ChartCard>

      <ChartCard
        className="lg:col-span-2"
        title={peak ? `Spending peaked in ${monthLabel(peak.month)} at ${money(peak.amount)}` : "Monthly spend"}
        subtitle="Approved and ordered requests per month, USD"
        howTo="Each bar is the total of requests approved or ordered in that month for this department. Hover a bar for the exact amount, or open the table."
        source={SOURCE}
        rows={months.map((m) => ({ month: m.month, usd: m.amount }))}
        raw={s.monthly}
        filename={`monthly-spend-${s.department}`}
        empty={<EmptyState title="No monthly spending yet">Monthly figures appear once requests are approved.</EmptyState>}
      >
        <div className="h-64">
          <ResponsiveContainer>
            <BarChart data={monthly} margin={{ top: 8, right: 8, bottom: 0, left: 8 }}>
              <CartesianGrid {...GRID} />
              <XAxis dataKey="month" {...AXIS} />
              <YAxis {...AXIS} tickFormatter={(v: number) => moneyCompact(v)} width={56} />
              <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip format={(v) => money(v)} />} />
              <Bar dataKey="amount" name="Spend" fill="var(--series-1)" {...BAR} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </ChartCard>

      <ChartCard
        className="lg:col-span-3"
        title={vendors.length ? `${vendors[0].vendor} accounts for ${pct(topShare)} of spend` : "Top vendors"}
        subtitle="Largest vendors by approved and ordered amount this fiscal year, USD"
        howTo="Bars are sorted from largest to smallest. Only the top eight vendors are shown; the table and CSV list the same eight."
        source={SOURCE}
        rows={vendors.map((v) => ({ vendor: v.vendor, vendor_id: v.vendor_id, usd: v.amount }))}
        raw={s.top_vendors}
        filename={`top-vendors-${s.department}`}
        empty={<EmptyState title="No vendor spending yet" />}
      >
        <div style={{ height: Math.max(vendors.length * 36 + 40, 120) }}>
          <ResponsiveContainer>
            <BarChart data={vendors} layout="vertical" margin={{ top: 0, right: 64, bottom: 0, left: 8 }}>
              <CartesianGrid {...GRID} horizontal={false} vertical />
              <XAxis type="number" {...AXIS} tickFormatter={(v: number) => moneyCompact(v)} />
              <YAxis type="category" dataKey="vendor" {...AXIS} width={180} />
              <Tooltip cursor={{ fill: "var(--surface-2)" }} content={<ChartTooltip format={(v) => money(v)} />} />
              <Bar dataKey="amount" name="Spend" fill="var(--series-1)" {...BAR_H} isAnimationActive={false} label={{ position: "right", fill: "var(--ink-2)", fontSize: 12, formatter: (v: unknown) => moneyCompact(Number(v)) }} />
            </BarChart>
          </ResponsiveContainer>
        </div>
      </ChartCard>
    </div>
  );
}
