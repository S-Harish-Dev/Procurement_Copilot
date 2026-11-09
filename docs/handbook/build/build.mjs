// Handbook PDF, fully offline: docs/handbook/0*.md + every ADR → one HTML page → Chromium → PDF.
// Mermaid is loaded from node_modules (no CDN) and rendered in the page before printing.
// Chromium: CHROME_PATH, else Playwright's installed Chromium (`npx playwright install chromium`).
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { marked } from 'marked';
import { chromium } from 'playwright-core';

const here = path.dirname(fileURLToPath(import.meta.url));
const docs = path.resolve(here, '..', '..');
const handbook = path.join(docs, 'handbook');
const out = path.join(docs, 'Procurement-Copilot-Handbook.pdf');

const read = (p) => fs.readFileSync(p, 'utf8');
const chapters = fs.readdirSync(handbook).filter((f) => /^\d\d-.*\.md$/.test(f)).sort();
const adrs = fs.readdirSync(path.join(docs, 'adr')).filter((f) => f.endsWith('.md')).sort();

// Mermaid blocks become <pre class="mermaid"> (rendered client-side); everything else is plain marked.
marked.use({ renderer: { code({ text, lang }) {
  if (lang === 'mermaid') return `<pre class="mermaid">${text}</pre>`;
  const esc = text.replace(/&/g, '&amp;').replace(/</g, '&lt;');
  return `<pre><code>${esc}</code></pre>`;
} } });

const sections = [];
for (const f of chapters) {
  if (f.startsWith('03-')) continue; // chapter 3 is generated from the ADRs below
  sections.push({ id: f.replace('.md', ''), html: marked.parse(read(path.join(handbook, f))) });
  if (f.startsWith('02-')) {
    const adrHtml = adrs.map((a) => `<section class="adr">${marked.parse(read(path.join(docs, 'adr', a)))}</section>`);
    sections.push({ id: '03-adrs', html: `<h1>3. Architecture decision records</h1>${adrHtml.join('\n')}` });
  }
}
const toc = sections.map((s) => {
  const m = /<h1[^>]*>(.*?)<\/h1>/.exec(s.html);
  return `<li><a href="#${s.id}">${m ? m[1] : s.id}</a></li>`;
}).join('');
const mermaidJs = pathToFileURL(path.join(here, 'node_modules/mermaid/dist/mermaid.min.js')).href;
const html = `<!doctype html><html><head><meta charset="utf-8"><title>Procurement Copilot Handbook</title>
<style>
  body { font: 10.5pt/1.5 system-ui, -apple-system, "Segoe UI", sans-serif; color: #0b0b0b; margin: 0; }
  h1 { font-size: 20pt; page-break-before: always; border-bottom: 1px solid #c3c2b7; padding-bottom: 4pt; }
  h2 { font-size: 14pt; margin-top: 18pt; } h3 { font-size: 11.5pt; }
  .adr h1 { font-size: 14pt; page-break-before: auto; border: none; margin-top: 24pt; }
  table { border-collapse: collapse; width: 100%; font-size: 9pt; margin: 8pt 0; page-break-inside: avoid; }
  th, td { border: 1px solid #e1e0d9; padding: 3pt 5pt; text-align: left; vertical-align: top; }
  th { background: #f0efec; }
  code { font: 9pt ui-monospace, Menlo, monospace; background: #f4f3ef; padding: 0 2pt; }
  pre { background: #f4f3ef; padding: 6pt; overflow-wrap: anywhere; white-space: pre-wrap; font-size: 8.5pt; }
  pre.mermaid { background: none; text-align: center; page-break-inside: avoid; }
  /* Tall diagrams (C4 containers, long sequences) must shrink to one page, never spill onto a blank one. */
  pre.mermaid svg { max-width: 100% !important; max-height: 235mm; width: auto; height: auto; }
  .cover { height: 90vh; display: flex; flex-direction: column; justify-content: center; }
  .cover h1 { page-break-before: auto; border: none; font-size: 30pt; }
  a { color: #1c5cab; text-decoration: none; }
</style></head><body>
<section class="cover"><h1>Procurement Copilot</h1><p>Learning handbook: architecture, components, decisions,
runbook, results, glossary, interview Q&amp;A.</p><p>Generated ${new Date().toISOString().slice(0, 10)} by
<code>make docs-pdf</code> from <code>docs/handbook</code> and <code>docs/adr</code>.</p></section>
<h1>Contents</h1><ol>${toc}</ol>
${sections.map((s) => `<section id="${s.id}">${s.html}</section>`).join('\n')}
<script src="${mermaidJs}"></script>
<script>mermaid.initialize({ startOnLoad: false, theme: 'neutral', securityLevel: 'strict',
  // Charts in the reference palette's first slot (the neutral theme's grey bars are too faint in print).
  themeVariables: { xyChart: { plotColorPalette: '#2a78d6' } } });
mermaid.run().then(() => { document.body.dataset.ready = '1'; }, (e) => { document.body.dataset.ready = 'error:' + e; });
</script></body></html>`;

const htmlPath = path.join(here, 'handbook.html');
fs.writeFileSync(htmlPath, html);
const browser = await chromium.launch({ executablePath: process.env.CHROME_PATH || undefined });
const page = await browser.newPage();
await page.goto(pathToFileURL(htmlPath).href);
await page.waitForFunction(() => document.body.dataset.ready, null, { timeout: 120000 });
const state = await page.evaluate(() => document.body.dataset.ready);
if (state !== '1') { console.error(`mermaid failed: ${state}`); process.exit(1); }
await page.pdf({ path: out, format: 'A4', printBackground: true,
  margin: { top: '18mm', bottom: '18mm', left: '16mm', right: '16mm' },
  displayHeaderFooter: true, headerTemplate: '<span></span>',
  footerTemplate: '<div style="font-size:8pt;width:100%;text-align:center;color:#898781">Procurement Copilot handbook · <span class="pageNumber"></span>/<span class="totalPages"></span></div>' });
await browser.close();
console.log(`wrote ${out} (${(fs.statSync(out).size / 1e6).toFixed(1)} MB, ${sections.length} sections, ${adrs.length} ADRs)`);
