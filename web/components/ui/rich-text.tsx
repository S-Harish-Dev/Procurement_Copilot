import { Fragment, type ReactNode } from "react";

// Retrieved passages and model answers often contain Markdown pipe tables. Render those as real
// tables; everything else stays plain text. shortcut: tables, **bold**, `code` and "* " bullets only,
// not full Markdown (bullets become inline "• " so citation chips stay on the same line); swap in a
// Markdown renderer if answers start needing headings or nested lists.

const isRow = (l: string) => /^\s*\|.*\|\s*$/.test(l);
const isSep = (l: string) => isRow(l) && /^[\s|:-]+$/.test(l) && l.includes("-");
const cells = (l: string) =>
  l
    .trim()
    .replace(/^\||\|$/g, "")
    .split("|")
    .map((c) => c.trim());

type Block = { kind: "text"; text: string } | { kind: "table"; head: string[]; rows: string[][] };

function parse(text: string): Block[] {
  const lines = text.split("\n");
  const out: Block[] = [];
  let buf: string[] = [];
  const flush = () => {
    if (buf.length) out.push({ kind: "text", text: buf.join("\n") });
    buf = [];
  };
  for (let i = 0; i < lines.length; i++) {
    if (isRow(lines[i]) && i + 1 < lines.length && isSep(lines[i + 1])) {
      flush();
      const head = cells(lines[i]);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && isRow(lines[i])) rows.push(cells(lines[i++]));
      i--; // the for-loop increments again
      out.push({ kind: "table", head, rows });
    } else buf.push(lines[i]);
  }
  flush();
  return out;
}

const bold = (s: string): ReactNode =>
  s.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((p, i) =>
    /^\*\*[^*]+\*\*$/.test(p) ? (
      <strong key={i}>{p.slice(2, -2)}</strong>
    ) : /^`[^`]+`$/.test(p) ? (
      <code key={i} className="rounded bg-surface-2 px-1 text-[0.9em]">
        {p.slice(1, -1)}
      </code>
    ) : (
      <Fragment key={i}>{p}</Fragment>
    ),
  );

/** Running text: Markdown bullets ("* x", "- x") become "• x", then bold and code are applied. */
const prose = (s: string): ReactNode => bold(s.replace(/^[ \t]*[*-][ \t]+/gm, "• "));

const numeric = (s: string) => /^[\s$€£+\-−~]*\d[\d,.\s]*(%|[KMB])?$/.test(s.replaceAll("*", ""));

function Table({ head, rows }: { head: string[]; rows: string[][] }) {
  // A column is right-aligned when every body cell in it is a number.
  const right = head.map((_, c) => rows.length > 0 && rows.every((r) => r[c] === undefined || r[c] === "" || numeric(r[c])));
  return (
    <div className="my-3 overflow-x-auto rounded-lg border border-line whitespace-normal">
      <table className="tabular w-full border-collapse text-sm">
        <thead className="bg-surface-2 text-left">
          <tr>
            {head.map((h, c) => (
              <th key={c} scope="col" className={`px-3 py-2 font-medium ${right[c] ? "text-right" : ""}`}>
                {bold(h)}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i} className="border-t border-line">
              {head.map((_, c) => (
                <td key={c} className={`px-3 py-1.5 ${right[c] ? "text-right" : ""}`}>
                  {bold(r[c] ?? "")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

/** Plain-text gist of a passage for small previews: its prose, plus the shape of any tables (never raw pipes). */
export function summarize(text: string): { prose: string; tables: { cols: number; rows: number }[] } {
  const blocks = parse(text);
  const prose = blocks
    .flatMap((b) => (b.kind === "text" ? [b.text] : []))
    .join(" ")
    .replace(/\*\*|`/g, "")
    .replace(/\s+/g, " ")
    .trim();
  const tables = blocks.flatMap((b) => (b.kind === "table" ? [{ cols: b.head.length, rows: b.rows.length }] : []));
  return { prose, tables };
}

/** Text with Markdown tables turned into real ones and bold, code and bullets applied. */
export function RichText({ text }: { text: string }) {
  const blocks = parse(text);
  if (blocks.length === 1 && blocks[0].kind === "text") return <>{prose(text)}</>;
  return (
    <>
      {/* Newlines around a table are layout, not content: trim them so the table sits flush. */}
      {blocks.map((b, i) => (b.kind === "table" ? <Table key={i} head={b.head} rows={b.rows} /> : <Fragment key={i}>{prose(b.text.replace(/^\n+|\n+$/g, ""))}</Fragment>))}
    </>
  );
}
