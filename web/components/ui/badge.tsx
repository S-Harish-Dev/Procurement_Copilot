import type { LucideIcon } from "lucide-react";
import type { ReactNode } from "react";

/**
 * One status vocabulary for the whole app: amber = waiting / needs attention, red = critical or failed,
 * orange = escalating, green = good or done, blue = information, violet = admin area. Always paired
 * with an icon or text, never colour alone.
 */
export type Tone = "neutral" | "good" | "warn" | "serious" | "crit" | "info" | "violet";

export const TONE: Record<Tone, string> = {
  neutral: "bg-surface-2 text-ink-2",
  good: "bg-good-soft text-success-text",
  warn: "bg-warn-soft text-warn-text",
  serious: "bg-serious-soft text-serious-text",
  crit: "bg-crit-soft text-danger-text",
  info: "bg-info-soft text-info-text",
  violet: "bg-violet-soft text-violet-text",
};

export function Badge({ tone = "neutral", icon: Icon, children, className = "" }: { tone?: Tone; icon?: LucideIcon; children: ReactNode; className?: string }) {
  return (
    <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium whitespace-nowrap ${TONE[tone]} ${className}`}>
      {Icon && <Icon aria-hidden className="size-3.5 shrink-0" />}
      {children}
    </span>
  );
}
