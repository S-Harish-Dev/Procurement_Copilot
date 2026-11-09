import type { LucideIcon } from "lucide-react";
import type { HTMLAttributes, ReactNode } from "react";
import { TONE, type Tone } from "./badge";

export function Card({ className = "", ...props }: HTMLAttributes<HTMLDivElement>) {
  return <div className={`rounded-xl border border-line bg-surface shadow-[0_1px_2px_rgba(11,11,11,0.05)] ${className}`} {...props} />;
}

/** Page title with an icon tile tinted by the section's tone (violet for admin Insights, blue elsewhere). */
export function PageHeader({ title, description, actions, icon: Icon, tone = "info" }: { title: string; description?: string; actions?: ReactNode; icon?: LucideIcon; tone?: Tone }) {
  return (
    <div className="mb-6 flex flex-wrap items-center justify-between gap-3">
      <div className="flex min-w-0 items-center gap-3">
        {Icon && (
          <span aria-hidden className={`inline-flex size-11 shrink-0 items-center justify-center rounded-xl ${TONE[tone]}`}>
            <Icon className="size-5" />
          </span>
        )}
        <div className="min-w-0">
          <h1 className="text-2xl font-semibold tracking-tight">{title}</h1>
          {description && <p className="mt-0.5 text-sm text-ink-2">{description}</p>}
        </div>
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}
