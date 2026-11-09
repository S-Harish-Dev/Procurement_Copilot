import { ChevronRight } from "lucide-react";

/** "A › B › C" section path as a breadcrumb. `max` keeps only the last N levels (with a leading "…"). */
export function Crumbs({ path, max, className = "" }: { path: string; max?: number; className?: string }) {
  if (!path) return null;
  const all = path.split("›").map((s) => s.trim()).filter(Boolean);
  const cut = max !== undefined && all.length > max;
  const parts = cut ? all.slice(-max) : all;
  return (
    <ol aria-label="Section" className={`flex flex-wrap items-center gap-x-1 gap-y-0.5 text-xs text-ink-2 ${className}`}>
      {cut && <li aria-hidden>…</li>}
      {parts.map((p, i) => (
        <li key={i} className="flex items-center gap-1">
          {(i > 0 || cut) && <ChevronRight aria-hidden className="size-3 shrink-0" />}
          <span className={i === parts.length - 1 ? "font-medium text-ink" : ""}>{p}</span>
        </li>
      ))}
    </ol>
  );
}
