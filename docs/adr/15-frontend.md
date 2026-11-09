# ADR-15: Frontend: Next.js App Router, static pages + client fetches, dataviz-validated charts

- Status: accepted (2026-10-30) · Detail: web/NOTES-M8.md

## Decision
- Next.js 16.4.3 (App Router), React 19.3.1, Tailwind 4.3.3, Recharts 3.10.1, TanStack Query 5.105.0.
  TypeScript 6.0.3, not 7.0.2: typescript-eslint (pulled in by eslint-config-next) refuses TS 7.0.
- No Next server routes: every page is static and fetches the gateway from the browser, so the gateway stays the
  only API and the only place authorization happens.
- JWT in `sessionStorage` (bearer only, never a cookie, so nothing for CSRF to ride on); conversations per user in
  `localStorage` (try/catch everywhere).
- SSE via `fetch` + a small parser (`EventSource` can't POST or send headers); one confirm/cancel helper for the chat
  ActionCard and `/approvals`.
- Charts follow the dataviz method: reference palette as CSS tokens with selected dark-mode steps, validated with
  the palette script in both modes; fixed colour per entity (guard layer, cache layer, provider) across charts and
  Grafana; takeaway titles, units, tooltips, CSV / raw JSON / table views.

## Why
Static + client fetch keeps the security boundary in one place and lets the UI be served by anything. Validated
palettes and table views make every number checkable and colour-blind safe.

## Measured
`web/scripts/verify.ts`: 183 contract checks against the live gateway, 0 problems. Real Chromium 156 with axe-core:
0 WCAG 2.1 AA violations on 10 pages in light and dark; no horizontal overflow at 390 px. Lighthouse `/login`:
accessibility 100, best practices 100, performance 98 (logged-in pages were checked with axe, not Lighthouse).
Rendering found a real crash (Chromium 156's `scrollIntoView()` returns a Promise) and six display bugs, fixed.

## Alternatives rejected
- *Server components fetching with a server-side token*: a second place holding credentials and enforcing access.
- *Cookie session*: needs CSRF protection the gateway otherwise doesn't need.
- *Recharts defaults / generated colours*: fail colour-blind separation and change colour when series change.
