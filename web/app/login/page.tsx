"use client";

import { useQuery } from "@tanstack/react-query";
import { Loader2, PackageCheck, Search, SearchX, Sparkles } from "lucide-react";
import { useRouter } from "next/navigation";
import { useMemo, useState } from "react";
import { ThemeToggle } from "@/components/theme-toggle";
import { TONE } from "@/components/ui/badge";
import { Card } from "@/components/ui/card";
import { EmptyState, ErrorState, Skeleton } from "@/components/ui/states";
import { GATEWAY } from "@/lib/api";
import { money } from "@/lib/format";
import { signIn } from "@/lib/use-session";
import type { Persona } from "@/lib/types";

const ROLES = [
  { value: "all", label: "Everyone", blurb: "Every demo person. Each sees only what their role and department allow." },
  { value: "employee", label: "Employees", blurb: "Ask policy questions and raise purchase requests for their department." },
  { value: "manager", label: "Managers", blurb: "Approve their team's requests up to their approval limit." },
  { value: "finance", label: "Finance", blurb: "Second-level approver; sees every department's budget." },
  { value: "admin", label: "Admins", blurb: "Final approver for large requests; sees Insights." },
];

// The five people that walk through the whole product, each with the one thing worth trying as them.
const QUICK_START: Record<string, string> = {
  "priya.sharma": "Ask for the hotel cap and get a cited answer.",
  "marcus.lee": "Ask about an IT contract: he gets the same “no information” as for a missing document.",
  "james.chen": "Approve a USD 15,000 request waiting on a manager.",
  "elena.petrova": "Give the second approval, then see the PO and e-mails.",
  "olivia.grant": "Open Insights: metrics, evals and request traces.",
};

const initials = (name: string) =>
  name
    .split(" ")
    .map((w) => w[0])
    .slice(0, 2)
    .join("");

function PersonaCard({ p, note, busy, disabled, onPick }: { p: Persona; note?: string; busy: boolean; disabled: boolean; onPick: (p: Persona) => void }) {
  const tone = p.role === "admin" ? "violet" : "info";
  return (
    <button
      type="button"
      onClick={() => onPick(p)}
      disabled={disabled}
      className="group flex h-full w-full flex-col rounded-xl border border-line bg-surface p-3.5 text-left shadow-[0_1px_2px_rgba(11,11,11,0.05)] transition-[border-color,box-shadow,transform] hover:-translate-y-px hover:border-btn hover:shadow-md disabled:opacity-60 disabled:hover:translate-y-0"
    >
      <span className="flex items-center gap-3">
        <span aria-hidden className={`inline-flex size-11 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${TONE[tone]}`}>
          {busy ? <Loader2 className="size-4 animate-spin" /> : initials(p.name)}
        </span>
        <span className="min-w-0">
          <span className="block truncate font-medium">{p.name}</span>
          <span className="block truncate text-xs text-ink-2 capitalize">
            {p.role} · {p.department}
          </span>
        </span>
      </span>
      {note && <span className="mt-3 block text-sm text-ink-2">{note}</span>}
      {/* Two label/value rows on every card: values right-align in one column, nothing wraps at any card
          width, and non-approvers read "None" instead of a missing chip. */}
      <span className="mt-auto block pt-3">
        <span className="block divide-y divide-line overflow-hidden rounded-lg border border-line bg-surface-2/60 text-xs">
          <span className="flex items-baseline justify-between gap-3 px-3 py-1.5">
            <span className="text-ink-2">Spend</span>
            <span className="tabular text-sm font-semibold">{money(p.spend_limit)}</span>
          </span>
          <span className="flex items-baseline justify-between gap-3 px-3 py-1.5">
            <span className="text-ink-2">Approves</span>
            <span className={`tabular text-sm ${p.approval_limit > 0 ? `font-semibold ${p.role === "admin" ? "text-violet-text" : "text-info-text"}` : "font-medium text-ink-2"}`}>
              {p.approval_limit > 0 ? money(p.approval_limit) : "None"}
            </span>
          </span>
        </span>
      </span>
    </button>
  );
}

export default function LoginPage() {
  const router = useRouter();
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [role, setRole] = useState("all");
  const [dept, setDept] = useState("all");
  const [query, setQuery] = useState("");
  const personas = useQuery({
    queryKey: ["personas"],
    queryFn: async () => {
      const r = await fetch(`${GATEWAY}/v1/auth/personas`);
      if (!r.ok) throw new Error(`The assistant service is not reachable (${r.status}).`);
      return (await r.json()) as Persona[];
    },
  });

  async function login(p: Persona) {
    setBusy(p.id);
    setError(null);
    try {
      const r = await fetch(`${GATEWAY}/v1/auth/login`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ user_id: p.id }),
      });
      if (!r.ok) throw new Error(`Sign-in failed (${r.status}).`);
      const { token } = (await r.json()) as { token: string };
      signIn({ token, user: p });
      router.push("/chat");
    } catch (e) {
      setError(e instanceof Error ? e.message : "Sign-in failed.");
      setBusy(null);
    }
  }

  const all = useMemo(() => personas.data ?? [], [personas.data]);
  const departments = useMemo(() => Array.from(new Set(all.map((p) => p.department))).sort(), [all]);
  const quick = Object.keys(QUICK_START).flatMap((id) => all.find((p) => p.id === id) ?? []);
  const q = query.trim().toLowerCase();
  const shown = all.filter((p) => (role === "all" || p.role === role) && (dept === "all" || p.department === dept) && (!q || `${p.name} ${p.department} ${p.role}`.toLowerCase().includes(q)));
  const count = (r: string) => all.filter((p) => r === "all" || p.role === r).length;

  return (
    <div className="login-backdrop min-h-dvh">
    <main className="mx-auto max-w-6xl px-4 pb-16">
      <div className="flex items-center justify-between py-5">
        {/* Brand lockup: the name plus one line saying what it is and who it is for. */}
        <span className="flex items-center gap-3">
          <span aria-hidden className="inline-flex size-12 items-center justify-center rounded-2xl bg-btn text-white shadow-lg ring-1 ring-white/15">
            <PackageCheck className="size-6" />
          </span>
          <span className="leading-tight">
            <span className="block text-lg font-semibold tracking-tight">Procurement Copilot</span>
            <span className="block text-sm text-ink-2">AI procurement assistant · Acme Corp</span>
          </span>
        </span>
        <ThemeToggle labels />
      </div>

      <header className="mx-auto max-w-2xl py-8 text-center sm:py-12">
        <h1 className="text-3xl font-semibold tracking-tight text-balance sm:text-4xl">Who do you want to sign in as?</h1>
        <p className="mt-3 text-ink-2 text-pretty">
          This is a demo: pick a person and the assistant answers, and acts, only within what their role and department allow. Switch person any time to compare.
        </p>
      </header>

      {error && (
        <p role="alert" className="mb-4 rounded-lg bg-crit-soft px-3 py-2 text-sm text-danger-text">
          {error}
        </p>
      )}
      {personas.isPending && (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5" aria-busy="true">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-40" />
          ))}
        </div>
      )}
      {personas.isError && <ErrorState error={personas.error} onRetry={() => personas.refetch()} />}

      {personas.data && (
        <>
          <section aria-labelledby="quick" className="mb-10">
            {/* Heading and hint travel together: the hint says what to do with the cards below it. */}
            <div className="mb-4 flex items-center gap-3">
              <span aria-hidden className="inline-flex size-10 shrink-0 items-center justify-center rounded-xl bg-info-soft text-info-text">
                <Sparkles className="size-5" />
              </span>
              <div>
                <h2 id="quick" className="text-lg leading-tight font-semibold">
                  Start here
                </h2>
                <p className="text-sm text-ink-2">Five people, one flow each. Pick one and try what’s on their card.</p>
              </div>
            </div>
            <ul className="rise grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5">
              {quick.map((p) => (
                <li key={p.id}>
                  <PersonaCard p={p} note={QUICK_START[p.id]} busy={busy === p.id} disabled={busy !== null} onPick={login} />
                </li>
              ))}
            </ul>
          </section>

          <section aria-labelledby="everyone">
            <Card className="p-4 sm:p-5">
              <h2 id="everyone" className="text-lg font-semibold">
                Everyone
              </h2>
              <div className="mt-3 flex flex-wrap items-center justify-between gap-3">
                <div role="radiogroup" aria-label="Filter by role" className="flex flex-wrap gap-1.5">
                  {ROLES.map((r) => {
                    const on = role === r.value;
                    return (
                      <button
                        key={r.value}
                        type="button"
                        role="radio"
                        aria-checked={on}
                        onClick={() => setRole(r.value)}
                        className={`inline-flex h-10 items-center gap-2 rounded-xl px-3.5 text-sm transition-colors ${on ? "bg-info-soft font-medium text-info-text" : "text-ink-2 hover:bg-surface-2 hover:text-ink"}`}
                      >
                        {r.label}
                        <span className={`tabular rounded-full px-1.5 text-xs ${on ? "bg-surface text-info-text" : "bg-surface-2"}`}>{count(r.value)}</span>
                      </button>
                    );
                  })}
                </div>
                <div className="flex flex-wrap gap-2">
                  <label className="relative">
                    <span className="sr-only">Search people</span>
                    <Search aria-hidden className="pointer-events-none absolute top-1/2 left-3 size-4 -translate-y-1/2 text-ink-2" />
                    <input
                      type="search"
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                      placeholder="Search people"
                      className="h-10 w-48 rounded-xl border border-line bg-surface pr-3 pl-9 text-sm placeholder:text-ink-2 focus-visible:border-btn"
                    />
                  </label>
                  <label>
                    <span className="sr-only">Department</span>
                    <select value={dept} onChange={(e) => setDept(e.target.value)} className="h-10 rounded-xl border border-line bg-surface px-3 text-sm">
                      <option value="all">All departments</option>
                      {departments.map((d) => (
                        <option key={d}>{d}</option>
                      ))}
                    </select>
                  </label>
                </div>
              </div>
              <p className="mt-3 text-sm text-ink-2" aria-live="polite">
                {ROLES.find((r) => r.value === role)?.blurb} <span className="tabular">Showing {shown.length}.</span>
              </p>
              {shown.length === 0 ? (
                <div className="mt-4">
                  <EmptyState title="No one matches">
                    <SearchX aria-hidden className="mx-auto mb-1 size-4" />
                    Try another role or department, or clear the search.
                  </EmptyState>
                </div>
              ) : (
                <ul className="mt-4 grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
                  {shown.map((p) => (
                    <li key={p.id}>
                      <PersonaCard p={p} busy={busy === p.id} disabled={busy !== null} onPick={login} />
                    </li>
                  ))}
                </ul>
              )}
            </Card>
          </section>
        </>
      )}
    </main>
    </div>
  );
}
