"use client";

import { useQuery } from "@tanstack/react-query";
import { Activity, ArrowLeftRight, Briefcase, ClipboardCheck, FileText, FlaskConical, LayoutDashboard, LogOut, MessageSquare, PackageCheck, Route, ShieldCheck, Wallet } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { tool } from "@/lib/api";
import { money } from "@/lib/format";
import { signOut } from "@/lib/use-session";
import type { Items, PendingRow, Persona } from "@/lib/types";
import { Badge } from "./ui/badge";
import { Menu, MenuGroup, MenuItem } from "./ui/menu";
import { ThemeToggle } from "./theme-toggle";

const initials = (name: string) =>
  name
    .split(" ")
    .map((w) => w[0])
    .slice(0, 2)
    .join("");

function Count({ n }: { n: number }) {
  return (
    <span className="inline-flex min-w-5 items-center justify-center rounded-full bg-warn-soft px-1.5 text-xs font-semibold text-warn-text tabular">
      {n}
      <span className="sr-only"> waiting for your decision</span>
    </span>
  );
}

export function Nav({ user }: { user: Persona }) {
  const path = usePathname();
  const router = useRouter();
  const canApprove = ["manager", "finance", "admin"].includes(user.role);
  // Same query key as the Approvals page, so the count and the list share one fetch.
  const pending = useQuery({ queryKey: ["pending"], queryFn: () => tool<Items<PendingRow>>("list_pending_approvals"), enabled: canApprove, refetchInterval: 60_000, retry: false });
  const waiting = pending.data?.count ?? 0;
  const on = (...prefixes: string[]) => prefixes.some((p) => path.startsWith(p));

  return (
    <header className="sticky top-0 z-30 border-b border-line bg-surface/90 backdrop-blur">
      {/* Same max width and gutter as <main>, so brand, content and page titles share one left edge. */}
      <div className="mx-auto grid max-w-7xl grid-cols-[auto_1fr_auto] items-center gap-2 px-4 py-2.5 md:gap-4">
        <Link href="/chat" className="flex items-center gap-2.5 font-semibold whitespace-nowrap" aria-label="Procurement Copilot home">
          <span aria-hidden className="inline-flex size-9 items-center justify-center rounded-xl bg-btn text-white shadow-sm">
            <PackageCheck className="size-5" />
          </span>
          <span className="hidden lg:inline">Procurement Copilot</span>
        </Link>

        {/* Below md the menus collapse to icons (labels stay for screen readers) so the bar never scrolls sideways. */}
        <nav aria-label="Main" className="flex items-center justify-center gap-0.5 md:gap-1">
          <Link
            href="/chat"
            aria-current={on("/chat") ? "page" : undefined}
            className={`inline-flex h-10 items-center gap-2 rounded-xl px-2.5 text-sm whitespace-nowrap transition-colors md:px-3 ${on("/chat") ? "bg-info-soft font-medium text-info-text" : "text-ink-2 hover:bg-surface-2 hover:text-ink"}`}
          >
            <MessageSquare aria-hidden className="size-4.5" />
            <span className="sr-only md:not-sr-only">Assistant</span>
          </Link>

          <Menu
            active={on("/requests", "/approvals", "/budgets")}
            trigger={
              <>
                <Briefcase aria-hidden className="size-4.5" />
                <span className="sr-only md:not-sr-only">Procurement</span>
                {waiting > 0 && <Count n={waiting} />}
              </>
            }
          >
            <MenuGroup>
              <MenuItem href="/requests" icon={FileText} label="My requests" description="Track your purchase requests and POs" current={on("/requests")} />
              {canApprove && <MenuItem href="/approvals" icon={ClipboardCheck} tone="warn" label="Approvals" description="Decide on requests waiting for you" current={on("/approvals")} badge={waiting > 0 ? <Count n={waiting} /> : undefined} />}
              <MenuItem href="/budgets" icon={Wallet} tone="good" label="Budgets" description="Spend, committed and remaining by department" current={on("/budgets")} />
            </MenuGroup>
          </Menu>

          {user.role === "admin" && (
            <Menu
              tone="violet"
              active={on("/insights")}
              trigger={
                <>
                  <Activity aria-hidden className="size-4.5" />
                  <span className="sr-only md:not-sr-only">Insights</span>
                </>
              }
            >
              <MenuGroup label="Performance">
                <MenuItem tone="violet" href="/insights/overview" icon={LayoutDashboard} label="Overview" description="Usage, speed, cost and what the guardrails stop" current={on("/insights/overview")} />
                <MenuItem tone="violet" href="/insights/traces" icon={Route} label="Traces" description="Stage-by-stage timing of recent requests" current={on("/insights/traces")} />
              </MenuGroup>
              <MenuGroup label="Quality">
                <MenuItem tone="violet" href="/insights/evals" icon={ShieldCheck} label="Evals" description="Retrieval, red-team and agent gates" current={on("/insights/evals")} />
                <MenuItem tone="violet" href="/insights/experiments" icon={FlaskConical} label="Experiments" description="Ablations behind the configuration choices" current={on("/insights/experiments")} />
              </MenuGroup>
            </Menu>
          )}
        </nav>

        <div className="flex items-center gap-2">
          <div className="hidden md:block">
            <ThemeToggle />
          </div>
          <Menu
            align="right"
            width="w-80"
            trigger={
              <>
                <span aria-hidden className={`inline-flex size-7 items-center justify-center rounded-full text-xs font-semibold ${user.role === "admin" ? "bg-violet-soft text-violet-text" : "bg-info-soft text-info-text"}`}>
                  {initials(user.name)}
                </span>
                <span className="hidden text-left leading-tight lg:block">
                  <span className="block text-sm font-medium text-ink">{user.name}</span>
                  <span className="block text-xs text-ink-2 capitalize">
                    {user.role} · {user.department}
                  </span>
                </span>
                <span className="sr-only lg:hidden">{user.name}, account menu</span>
              </>
            }
          >
            {/* Identity in one row, limits as a two-up grid: four wrapping chips used four lines for the same facts. */}
            <div className="flex items-center gap-3 px-3 pt-2 pb-2.5">
              <span aria-hidden className={`inline-flex size-11 shrink-0 items-center justify-center rounded-full text-sm font-semibold ${user.role === "admin" ? "bg-violet-soft text-violet-text" : "bg-info-soft text-info-text"}`}>
                {initials(user.name)}
              </span>
              <div className="min-w-0">
                <p className="truncate font-medium">{user.name}</p>
                <p className="truncate text-xs text-ink-2">{user.email}</p>
                <p className="mt-1 flex items-center gap-1.5 text-xs text-ink-2">
                  <Badge tone={user.role === "admin" ? "violet" : "info"} className="px-2 py-px capitalize">
                    {user.role}
                  </Badge>
                  {user.department}
                </p>
              </div>
            </div>
            <dl className="mx-2 mb-2 grid grid-cols-2 gap-2 text-xs">
              <div className="rounded-lg bg-surface-2 px-3 py-2">
                <dt className="text-ink-2">Spend limit</dt>
                <dd className="tabular text-sm font-semibold">{money(user.spend_limit)}</dd>
              </div>
              <div className={`rounded-lg px-3 py-2 ${user.approval_limit > 0 ? "bg-good-soft" : "bg-surface-2"}`}>
                <dt className="text-ink-2">Approves up to</dt>
                <dd className={`tabular text-sm font-semibold ${user.approval_limit > 0 ? "text-success-text" : "text-ink-2"}`}>{user.approval_limit > 0 ? money(user.approval_limit) : "No approvals"}</dd>
              </div>
            </dl>
            {/* The top bar has no room for the theme control on phones; it lives here instead. */}
            <div className="px-3 pb-3 md:hidden">
              <p className="mb-1.5 text-xs font-medium text-ink-2">Theme</p>
              <ThemeToggle />
            </div>
            <div role="group" className="grid grid-cols-2 gap-2 border-t border-line p-2 pb-0.5">
              <Link href="/login" role="menuitem" title="Compare what another role sees" className="flex h-10 items-center justify-center gap-2 rounded-xl bg-info-soft text-sm font-medium text-info-text transition-colors hover:brightness-95 focus-visible:brightness-95">
                <ArrowLeftRight aria-hidden className="size-4" />
                Switch person
              </Link>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  signOut();
                  router.push("/login");
                }}
                className="flex h-10 items-center justify-center gap-2 rounded-xl bg-crit-soft text-sm font-medium text-danger-text transition-colors hover:brightness-95 focus-visible:brightness-95"
              >
                <LogOut aria-hidden className="size-4" />
                Sign out
              </button>
            </div>
          </Menu>
        </div>
      </div>
    </header>
  );
}
