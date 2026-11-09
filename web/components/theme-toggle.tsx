"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useSyncExternalStore } from "react";

type Theme = "system" | "light" | "dark";
const OPTIONS: { value: Theme; label: string; icon: typeof Sun }[] = [
  { value: "light", label: "Light", icon: Sun },
  { value: "dark", label: "Dark", icon: Moon },
  { value: "system", label: "System", icon: Monitor },
];
const listeners = new Set<() => void>();

function read(): Theme {
  try {
    const t = localStorage.getItem("pc.theme");
    return t === "light" || t === "dark" ? t : "system";
  } catch {
    return "system";
  }
}

function apply(t: Theme) {
  try {
    if (t === "system") localStorage.removeItem("pc.theme");
    else localStorage.setItem("pc.theme", t);
  } catch {
    /* storage blocked: the choice still applies to this page */
  }
  if (t === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
  listeners.forEach((l) => l());
}

/** Light / Dark / System as one visible segmented control: every option one click away, 36px targets. */
export function ThemeToggle({ labels = false }: { labels?: boolean }) {
  const theme = useSyncExternalStore(
    (cb) => {
      listeners.add(cb);
      return () => listeners.delete(cb);
    },
    read,
    () => "system" as Theme,
  );
  return (
    <div role="radiogroup" aria-label="Theme" className="inline-flex rounded-xl bg-surface-2 p-1">
      {OPTIONS.map(({ value, label, icon: Icon }) => {
        const on = theme === value;
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={on}
            aria-label={label}
            title={label}
            onClick={() => apply(value)}
            className={`inline-flex h-9 min-w-9 items-center justify-center gap-1.5 rounded-lg px-2.5 text-sm transition-colors ${on ? "bg-surface font-medium text-ink shadow-sm" : "text-ink-2 hover:text-ink"}`}
          >
            <Icon aria-hidden className="size-4.5" />
            {labels && <span className="hidden sm:inline">{label}</span>}
          </button>
        );
      })}
    </div>
  );
}
