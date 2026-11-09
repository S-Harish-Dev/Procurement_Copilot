"use client";

import { Info } from "lucide-react";
import { useId, useState, type ReactNode } from "react";

/** Icon-only (i): opens on hover, focus or tap, closes on leave, blur or Escape. No visible label. */
export function InfoPopover({ children, label = "More information" }: { children: ReactNode; label?: string }) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <span className="relative inline-flex" onMouseEnter={() => setOpen(true)} onMouseLeave={() => setOpen(false)}>
      <button
        type="button"
        aria-label={label}
        aria-expanded={open}
        aria-describedby={open ? id : undefined}
        onClick={() => setOpen(true)}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={(e) => e.key === "Escape" && setOpen(false)}
        className="inline-flex size-7 items-center justify-center rounded-full text-muted transition-colors hover:bg-info-soft hover:text-info-text focus-visible:bg-info-soft focus-visible:text-info-text"
      >
        <Info aria-hidden className="size-3.5" />
      </button>
      {open && (
        <span
          id={id}
          role="tooltip"
          className="absolute top-full right-0 z-20 mt-1 block w-72 max-w-[calc(100vw-2rem)] rounded-lg border border-line bg-surface p-3 text-left text-xs leading-relaxed font-normal text-ink-2 shadow-lg"
        >
          {children}
        </span>
      )}
    </span>
  );
}
