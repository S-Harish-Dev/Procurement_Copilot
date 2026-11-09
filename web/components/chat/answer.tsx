"use client";

import { Table2 } from "lucide-react";
import { useEffect, useId, useRef, useState } from "react";
import { createPortal } from "react-dom";
import { RichText, summarize } from "@/components/ui/rich-text";
import type { Source } from "@/lib/types";
import { Crumbs } from "./source-meta";

const PREVIEW_W = 340;
const EDGE = 12;

/**
 * Hover/focus preview of a cited passage. Rendered into <body> with fixed positioning so it is never
 * clipped by the scrolling chat pane; flips below the chip when there is no room above, and is clamped
 * to the viewport horizontally.
 */
function Preview({ id, n, source, anchor }: { id: string; n: number; source: Source; anchor: DOMRect }) {
  const { prose, tables } = summarize(source.snippet);
  const left = Math.min(Math.max(anchor.left + anchor.width / 2 - PREVIEW_W / 2, EDGE), window.innerWidth - PREVIEW_W - EDGE);
  const above = anchor.top > 280;
  const pos = above ? { bottom: window.innerHeight - anchor.top + 8 } : { top: anchor.bottom + 8 };
  return createPortal(
    <div id={id} role="tooltip" style={{ left, width: PREVIEW_W, ...pos }} className="pointer-events-none fixed z-[100] rounded-xl border border-line bg-surface p-3.5 text-left shadow-2xl">
      <div className="flex items-start gap-2.5">
        <span aria-hidden className="inline-flex size-6 shrink-0 items-center justify-center rounded-md bg-info-soft text-xs font-semibold text-info-text">
          {n}
        </span>
        <div className="min-w-0">
          <p className="text-sm leading-snug font-semibold">{source.title}</p>
          <Crumbs path={source.section_path} max={2} className="mt-0.5" />
        </div>
      </div>
      {(prose || tables.length > 0) && (
        <div className="mt-2.5 space-y-2 border-t border-line pt-2.5 text-xs leading-relaxed text-ink-2">
          {prose && <p className="line-clamp-4">{prose}</p>}
          {tables.map((t, i) => (
            <p key={i} className="inline-flex items-center gap-1.5 rounded-md bg-surface-2 px-2 py-1 font-medium text-ink">
              <Table2 aria-hidden className="size-3.5" />
              Table · {t.cols} columns · {t.rows} rows
            </p>
          ))}
        </div>
      )}
      <p className="mt-2.5 flex items-center justify-between gap-2 border-t border-line pt-2 text-[11px] text-ink-2">
        <span className="truncate">
          {source.source} · {source.doc_id}
        </span>
        <span className="shrink-0 font-medium text-info-text">Click for full passage</span>
      </p>
    </div>,
    document.body,
  );
}

/** A `[S1]` citation: hover/focus previews the passage, click opens the side panel. */
function CitationChip({ n, source, onOpen }: { n: number; source?: Source; onOpen: (s: Source) => void }) {
  const [anchor, setAnchor] = useState<DOMRect | null>(null);
  const btn = useRef<HTMLButtonElement>(null);
  const id = useId();
  // A scrolling pane moves the chip out from under the fixed preview: close it instead of letting it drift.
  useEffect(() => {
    if (!anchor) return;
    const close = () => setAnchor(null);
    window.addEventListener("scroll", close, true);
    window.addEventListener("resize", close);
    return () => {
      window.removeEventListener("scroll", close, true);
      window.removeEventListener("resize", close);
    };
  }, [anchor]);
  if (!source) return <span className="text-ink-2">[{n}]</span>;
  const show = () => setAnchor(btn.current?.getBoundingClientRect() ?? null);
  return (
    <span className="inline-block">
      <button
        ref={btn}
        type="button"
        onClick={() => onOpen(source)}
        onMouseEnter={show}
        onMouseLeave={() => setAnchor(null)}
        onFocus={show}
        onBlur={() => setAnchor(null)}
        aria-describedby={anchor ? id : undefined}
        aria-label={`Source ${n}: ${source.title}`}
        className="mx-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-md bg-info-soft px-1 align-text-top text-xs font-semibold text-info-text transition-colors hover:bg-btn hover:text-white"
      >
        {n}
      </button>
      {anchor && <Preview id={id} n={n} source={source} anchor={anchor} />}
    </span>
  );
}

export function Answer({ text, sources, onOpen }: { text: string; sources: Source[]; onOpen: (s: Source) => void }) {
  const byId = new Map(sources.map((s) => [s.id, s]));
  const parts = text.split(/(\[S\d+\])/g);
  return (
    <div className="leading-relaxed whitespace-pre-wrap">
      {parts.map((p, i) => {
        const m = /^\[S(\d+)\]$/.exec(p);
        return m ? <CitationChip key={i} n={Number(m[1])} source={byId.get(`S${m[1]}`)} onOpen={onOpen} /> : <RichText key={i} text={p} />;
      })}
    </div>
  );
}
