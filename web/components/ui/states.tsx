import { AlertTriangle, Inbox } from "lucide-react";
import type { ReactNode } from "react";

export function Skeleton({ className = "" }: { className?: string }) {
  return <div aria-hidden className={`animate-pulse rounded-xl bg-surface-2 ${className}`} />;
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-line bg-surface/60 px-6 py-12 text-center">
      <span aria-hidden className="inline-flex size-11 items-center justify-center rounded-full bg-info-soft text-info-text">
        <Inbox className="size-5" />
      </span>
      <p className="font-medium">{title}</p>
      {children && <div className="max-w-md text-sm text-ink-2">{children}</div>}
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <div role="alert" className="flex flex-col items-center gap-2 rounded-xl border border-critical/30 bg-crit-soft px-6 py-8 text-center">
      <AlertTriangle aria-hidden className="size-6 text-danger-text" />
      <p className="font-medium">Couldn’t load this</p>
      <p className="max-w-md text-sm text-ink-2">{message}</p>
      {onRetry && (
        <button type="button" onClick={onRetry} className="mt-1 rounded-lg border border-line bg-surface px-3 py-1.5 text-sm font-medium text-ink hover:bg-surface-2">
          Try again
        </button>
      )}
    </div>
  );
}
