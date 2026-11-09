# Load test report

Run `20261030T175853Z` on `master` (64 cores, shared machine, load average before: 29.14 26.75 20.36), git `a44929d`, 60s per scenario. Offline: mock LLM and mock Jev. Per-user rate limits lifted for the run. Raw k6 summaries: `evals/loadtest/20261030T175853Z/`.

| Scenario | Path | VUs | RPS | p50 ms | p95 ms | p99 ms | Checks OK | Target |
|---|---|---|---|---|---|---|---|---|
| `cache_hit` | Exact answer-cache hit (full gateway path, no retrieval/LLM) | 64 | 4,679 | 12.4 | 25.5 | 33.8 | 100.0% | 1-5k RPS |
| `retrieval` | Retrieval only: embed + BM25 + hybrid Qdrant with ACL filter | 100 | 53 | 1,841.9 | 2,021.7 | 2,048.2 | 100.0% | p95 < 100 ms at 100 VUs |
| `e2e_mock` | Full Q&A, caches off, mock Jev + mock LLM | 32 | 24 | 1,460.5 | 2,271.6 | 2,312.5 | 100.0% | reported (no target) |

Interpretation and the bottleneck analysis are in Limitation.md §12.
