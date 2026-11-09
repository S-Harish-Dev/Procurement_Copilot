"use client";

import { useEffect, useRef, type ReactNode } from "react";
import { X } from "lucide-react";

/** Right-hand side panel (citations). Native <dialog> gives focus trapping and Esc for free. */
export function Sheet({ open, onClose, title, children }: { open: boolean; onClose: () => void; title: string; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const d = ref.current;
    if (!d) return;
    if (open && !d.open) d.showModal();
    if (!open && d.open) d.close();
  }, [open]);
  return (
    <dialog
      ref={ref}
      onClose={onClose}
      aria-label={title}
      className="m-0 ml-auto h-dvh max-h-dvh w-full max-w-md border-l border-line bg-surface p-0 text-ink backdrop:bg-black/30"
    >
      {/* Title truly centred: equal-width side columns, close button on the right. */}
      <div className="grid grid-cols-[2.5rem_1fr_2.5rem] items-center border-b border-line px-3 py-3">
        <span aria-hidden />
        <h2 className="text-center font-semibold">{title}</h2>
        <button type="button" onClick={onClose} aria-label="Close" className="justify-self-end rounded-lg p-2 text-ink-2 hover:bg-surface-2 hover:text-ink">
          <X className="size-5" />
        </button>
      </div>
      <div className="overflow-y-auto p-5">{children}</div>
    </dialog>
  );
}
