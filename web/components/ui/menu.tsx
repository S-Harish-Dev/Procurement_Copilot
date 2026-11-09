"use client";

import { ChevronDown, type LucideIcon } from "lucide-react";
import Link from "next/link";
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { TONE, type Tone } from "./badge";

/**
 * Dropdown menu: a button that opens a panel of links. Closes on outside click, Escape and
 * navigation; ArrowUp / ArrowDown / Home / End move between items. Hands focus back to the button.
 */
export function Menu({ trigger, active = false, tone = "info", align = "left", width = "w-80", children }: { trigger: ReactNode; active?: boolean; tone?: Tone; align?: "left" | "right"; width?: string; children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const root = useRef<HTMLDivElement>(null);
  const id = useId();

  useEffect(() => {
    if (!open) return;
    const down = (e: MouseEvent) => {
      if (!root.current?.contains(e.target as Node)) setOpen(false);
    };
    const key = (e: globalThis.KeyboardEvent) => {
      if (e.key === "Escape") {
        setOpen(false);
        root.current?.querySelector<HTMLElement>("button")?.focus();
      }
    };
    document.addEventListener("mousedown", down);
    document.addEventListener("keydown", key);
    return () => {
      document.removeEventListener("mousedown", down);
      document.removeEventListener("keydown", key);
    };
  }, [open]);

  const items = () => Array.from(root.current?.querySelectorAll<HTMLElement>('[role="menuitem"]') ?? []);
  const move = (e: KeyboardEvent, to: (list: HTMLElement[], i: number) => number) => {
    const list = items();
    if (!list.length) return;
    e.preventDefault();
    list[(to(list, list.indexOf(document.activeElement as HTMLElement)) + list.length) % list.length]?.focus();
  };

  return (
    <div ref={root} className="relative">
      <button
        type="button"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? id : undefined}
        onClick={() => setOpen((o) => !o)}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown") {
            e.preventDefault();
            setOpen(true);
            requestAnimationFrame(() => items()[0]?.focus());
          }
        }}
        className={`inline-flex h-10 items-center gap-2 rounded-xl px-3 text-sm whitespace-nowrap transition-colors ${active ? `${TONE[tone]} font-medium` : open ? "bg-surface-2 text-ink" : "text-ink-2 hover:bg-surface-2 hover:text-ink"}`}
      >
        {trigger}
        <ChevronDown aria-hidden className={`size-4 transition-transform ${open ? "rotate-180" : ""}`} />
      </button>
      {open && (
        <div
          id={id}
          role="menu"
          onClick={(e) => (e.target as HTMLElement).closest("a") && setOpen(false)}
          onKeyDown={(e) => {
            if (e.key === "ArrowDown") move(e, (_, i) => i + 1);
            else if (e.key === "ArrowUp") move(e, (_, i) => i - 1);
            else if (e.key === "Home") move(e, () => 0);
            else if (e.key === "End") move(e, (l) => l.length - 1);
          }}
          className={`absolute top-full z-40 mt-2 max-w-[calc(100vw-2rem)] rounded-2xl border border-line bg-surface p-2 shadow-xl ${width} ${align === "right" ? "right-0" : "left-0"}`}
        >
          {children}
        </div>
      )}
    </div>
  );
}

export function MenuGroup({ label, children }: { label?: string; children: ReactNode }) {
  return (
    <div className="[&+&]:mt-1 [&+&]:border-t [&+&]:border-line [&+&]:pt-1" role="group" aria-label={label}>
      {label && <p className="px-3 pt-2 pb-1 text-xs font-medium text-ink-2">{label}</p>}
      {children}
    </div>
  );
}

export function MenuItem({ href, icon: Icon, label, description, badge, current, tone = "info" }: { href: string; icon: LucideIcon; label: string; description?: string; badge?: ReactNode; current?: boolean; tone?: Tone }) {
  return (
    <Link
      href={href}
      role="menuitem"
      aria-current={current ? "page" : undefined}
      className={`flex items-center gap-3 rounded-xl px-3 py-2.5 transition-colors hover:bg-surface-2 focus-visible:bg-surface-2 ${current ? "bg-surface-2" : ""}`}
    >
      <span aria-hidden className={`inline-flex size-9 shrink-0 items-center justify-center rounded-lg ${TONE[tone]}`}>
        <Icon className="size-4.5" />
      </span>
      <span className="min-w-0 flex-1">
        <span className="block text-sm font-medium">{label}</span>
        {description && <span className="block text-xs text-ink-2">{description}</span>}
      </span>
      {badge}
    </Link>
  );
}
