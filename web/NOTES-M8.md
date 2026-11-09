# Web UI (M8): notes

Next.js App Router app in `web/`, talking to the gateway from the browser with a bearer token.
The contract is `docs/api.md`. Make targets: `web-install`, `web-dev`, `web-build`, `web-start`,
`web-lint`, `web-verify`.

## Pages
- User view (every role): `/login` (persona cards), `/chat`, `/requests`, `/requests/[id]`, `/approvals`
  (manager/finance/admin), `/budgets`.
- Admin "Insights": `/insights/overview`, `/insights/evals`, `/insights/experiments`, `/insights/traces`,
  `/insights/traces/[id]`. The nav hides them from non-admins and the insights layout refuses to render them.
  The gateway enforces it anyway (403).

## Versions (exact pins; `package-lock.json` committed)
next 16.4.3 · react / react-dom 19.3.1 · tailwindcss + @tailwindcss/postcss 4.3.3 · recharts 3.10.1 ·
@tanstack/react-query 5.105.0 · lucide-react 1.65.0 · eslint 10.14.0 + eslint-config-next 16.4.3 · Node 22.23.1.

Downgrade: TypeScript 7.0.2 → 6.0.3. `tsc` 7 type-checks this app fine, but `eslint-config-next`
pulls in typescript-eslint, which refuses to start on TS 7.0 ("typescript-eslint does not support TS 7.0";
upstream tracks TS ≥ 7.1 support). `next build` also type-checks through the TS API. 6.0.3 is the newest 6.x.
Revisit when typescript-eslint supports 7.x.

## Decisions (and why)
- JWT in `sessionStorage`, not a cookie. The gateway accepts bearer tokens only, so there is nothing for CSRF to
  ride on. The token is scoped to the tab and gone when it closes. A 401 clears it and returns to `/login`.
- Session read with `useSyncExternalStore`: server snapshot `null`, real value after hydration, no
  setState-in-effect flash (React 19 lint rules forbid that pattern anyway). Authenticated pages render only
  client-side (the `(app)` layout shows a skeleton until the session is read).
- SSE via `fetch` + a hand-written parser (`lib/sse.ts`): `EventSource` can't POST or send an
  Authorization header. The parser has no `@/` imports, so `scripts/verify.ts` runs the exact same code in Node.
- Conversations in `localStorage` per user (max 30, every access in try/catch). Chat history is a per-browser
  convenience; the gateway is stateless per request. Half-streamed messages are never persisted as final.
- Tool events: the gateway sends `phase: "started"` then `"done"` for every tool call; one chip per call.
  Write tools read "Prepared … for your review", never "Looked up".
- Empty turns: if a turn ends with neither text nor an action card (the offline mock LLM does this for
  natural-language action requests), the UI says "I couldn't complete that request…" instead of an empty bubble.
- One confirm/cancel helper (`lib/actions.ts`) for both the chat ActionCard and `/approvals`; proposals are
  never executed without the Confirm click.
- No Next server routes: everything is static plus client fetches, so the gateway stays the only API and the
  single place authorization happens.
- Monthly spend gaps filled with zeros client-side (the summary skips empty months); a gap reads as a zero
  bar, not a missing month.
- Retries: queries retry twice with backoff (the gateway restarts during development); refetch on window focus
  is off.
- Cache hits chart uses stacked bars, not the stacked area I first planned: hits are per-interval counts. An area
  interpolates across empty intervals and draws nothing for a single sample, which is exactly what early
  Prometheus data looks like.

## Charts (dataviz method)
- Palette: the reference palette verbatim as CSS custom properties (`app/globals.css`), light and dark steps
  under both `prefers-color-scheme` and `data-theme`. Entities keep fixed slots everywhere (`lib/palette.ts`):
  guard layers, cache layers, providers (mock-primary/secondary take the slots of the providers they stand in
  for), router intents/tiers, cost kinds, budget parts (remaining = neutral gray).
- Single-series charts use slot 1 everywhere (no value ramp on nominal categories); ordinal fills (confusion
  matrix) use the blue sequential steps 250/550; status colours appear only with an icon and a label
  (status pills, pass/fail chips, approval steps).
- Every chart card has a computed takeaway title, units in the subtitle, "ⓘ How to read this", source + last
  updated, Table (the accessible twin), Raw JSON and CSV. Recessive solid hairline grid, 2px lines,
  bars ≤ 24px with 4px rounded data ends, custom tooltip (value first, line key), legends only for ≥ 2 series.
  One axis per chart. Ratios on the evals small multiples are clamped to [0, 100%].
- Buttons use sequential step 500 (`#256abf`) so white text keeps ≥ 4.5:1 in both themes (slot-1 blue doesn't).

Validator output (`scripts/validate_palette.js`, reference palette):
```
Palette (light, surface #fcfcfb, categorical): 8 slots
  [PASS] Lightness band         all 8 inside L 0.43–0.77
  [PASS] Chroma floor           all 8 >= 0.1
  [PASS] CVD separation         worst adjacent #eda100↔#1baf7a ΔE 9.1 (protan) · tritan 5.8
  [PASS] Normal-vision floor    worst adjacent #e87ba4↔#eda100 ΔE 19.6 (normal)
  [WARN] Contrast vs surface    below 3:1 — relief required (visible labels or table view): [["#1baf7a",2.74],["#eda100",2.11],["#e87ba4",2.62]]
  → ALL CHECKS PASS

Palette (dark, surface #1a1a19, categorical): 8 slots
  [PASS] Lightness band         all 8 inside L 0.48–0.67
  [PASS] Chroma floor           all 8 >= 0.1
  [PASS] CVD separation         worst adjacent #c98500↔#199e70 ΔE 8.4 (protan) · tritan 8.7
  [PASS] Normal-vision floor    worst adjacent #d55181↔#c98500 ΔE 19.3 (normal)
  [PASS] Contrast vs surface    all 8 >= 3:1
  → ALL CHECKS PASS
```
The light-mode contrast WARN (aqua, yellow, magenta) is relieved by legends with values and the Table view on
every card.

## Verified (2026-10-30, against the live gateway)
- `npm run typecheck`, `npm run lint`, `npm run build`: clean (12 routes).
- `npm run verify` (`scripts/verify.ts`): 183 contract checks, 0 problems, covering personas/login, chat SSE
  order and shapes, no diagnostics for non-admins, feedback, every tool the pages use, an employee denied
  another department's spend summary, approval proposal + cancel, chat create proposal (scripted form) +
  cancel, all admin endpoints and their shapes, 403 for an employee on `/admin/*`, CORS for the UI origin.
- Real browser (Playwright 1.64.1, Chromium 156 headless shell, kept out of the repo): 10 pages × light/dark as
  the right persona plus mobile login. 0 console errors, 0 axe-core WCAG 2.1 A/AA violations on the 12
  audited views; no horizontal overflow at 390px. Interactions: the answer skeleton is visible 74 ms after
  sending, the citation chip opens the source panel, the ActionCard renders and Cancel works, conversations
  persist, the approvals proposal card works, and an employee sees no Insights links and is refused the page.
  Screenshots were reviewed by eye; issues found that way were fixed, as listed below.
- Bugs found by rendering, fixed: (1) the chat crashed on the first answer in Chromium 156:
  `scrollIntoView()` now returns a Promise, and an expression-bodied `useEffect` handed it to React as a
  "cleanup". Effects are now block-bodied. (2) "kept ≥ 1 passage" read 100% because the zero bucket is `"0.0"`,
  not `"0"`; buckets are now compared numerically. (3) Mock providers fell to gray. (4) Negative savings read
  "saved −46%". (5) Duplicate % ticks on small scales. (6) A $90 request was invisible on a $2M budget bar.

## Known gaps
- Lighthouse score not run: axe-core (the engine behind Lighthouse's accessibility category) found 0 violations,
  but the ≥ 90 Lighthouse number itself still needs a Lighthouse run (laptop Chrome).
- With the offline mock LLM, natural-language action requests produce no proposal (only the scripted
  `[[call …]]` form does) and the mock echoes "Result: {json}" after a proposal; a real LLM phrases both.
- Experiments render generically from two shapes: `data.bars: [{label, value}]` (+ `unit`), or
  `data.curves: [{name, points: [[x, y]]}]` with `x_label`, `y_label`, `chosen: {x, y, label}`. Anything else
  shows raw JSON. M10 should emit these shapes.
- Router "cost per tier" isn't shown: the overview contract has counts per tier only.
- The traces waterfall shows the gateway's own stage spans; with no Tempo in the native stack there is no Grafana link
  (`grafana_url` null).
