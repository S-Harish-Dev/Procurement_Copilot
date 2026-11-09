"use client";

import { createContext, useCallback, useContext, useState, type ReactNode } from "react";
import { AlertCircle, CheckCircle2, Info, X } from "lucide-react";
import { TONE, type Tone } from "./badge";

interface Toast {
  id: number;
  title: string;
  body?: ReactNode;
  tone: "info" | "success" | "error";
}

// Success = green, error = red, info = blue; the icon carries the meaning too, not colour alone.
const LOOK: Record<Toast["tone"], { icon: typeof Info; tone: Tone }> = {
  success: { icon: CheckCircle2, tone: "good" },
  error: { icon: AlertCircle, tone: "crit" },
  info: { icon: Info, tone: "info" },
};

const Ctx = createContext<(t: Omit<Toast, "id">) => void>(() => {});

export function useToast() {
  return useContext(Ctx);
}

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const push = useCallback((t: Omit<Toast, "id">) => {
    const id = Date.now() + Math.random();
    setToasts((all) => [...all, { ...t, id }]);
    setTimeout(() => setToasts((all) => all.filter((x) => x.id !== id)), 7000);
  }, []);
  return (
    <Ctx.Provider value={push}>
      {children}
      {/* Polite live region: results are announced without stealing focus. */}
      <div aria-live="polite" className="pointer-events-none fixed right-4 bottom-4 z-50 flex w-80 max-w-[calc(100vw-2rem)] flex-col gap-2">
        {toasts.map((t) => {
          const { icon: Icon, tone } = LOOK[t.tone];
          return (
            <div key={t.id} role="status" className="pointer-events-auto flex items-start gap-3 rounded-xl border border-line bg-surface p-3 shadow-lg">
              <span aria-hidden className={`inline-flex size-8 shrink-0 items-center justify-center rounded-lg ${TONE[tone]}`}>
                <Icon className="size-4.5" />
              </span>
              <div className="min-w-0 flex-1">
                <p className="text-sm font-medium">{t.title}</p>
                {t.body && <div className="mt-0.5 text-sm text-ink-2">{t.body}</div>}
              </div>
              <button type="button" aria-label="Dismiss" onClick={() => setToasts((all) => all.filter((x) => x.id !== t.id))} className="rounded p-0.5 text-ink-2 hover:text-ink">
                <X className="size-4" />
              </button>
            </div>
          );
        })}
      </div>
    </Ctx.Provider>
  );
}
