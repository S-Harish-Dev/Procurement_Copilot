# ADR-14: Evaluation: deterministic offline gates on every commit, model-quality evals with keys

- Status: accepted (2026-10-30)

## Decision
| Suite | What it measures | Gate |
|---|---|---|
| `make eval-fast` (seconds) | Retrieval through the gateway's own path (`/admin/eval/retrieve`): recall@20, hit@5, MRR, nDCG@10 with seeded bootstrap 95% CIs on 263 cases; local guard detection/FPR via `/admin/eval/guard` | Hard: forbidden-doc retrievals = 0, ACL leaks = 0 (re-checked against OPA directly), OPA test failures = 0. Regression: drop > tolerance and baseline outside the CI |
| `make redteam` | 144 attacks (8 categories, obfuscations) + 150 benign | leaks, PII, prompt leak, unauthorized actions, template mismatch, HTTP abuse = 0 |
| `make agent-tasks` | 37 agent tasks through gateway → brain → MCP | unauthorized_actions = 0 |
| `make cache-check` | scope isolation, sharing, semantic, index-bump invalidation | cross_scope_serves = 0 |
| `make chaos`, `make loadtest` | resilience drills, k6 throughput/latency | drills pass; report committed |
| `make experiments` | M10 ablations and threshold sweeps | informs decisions |

Datasets are built from ground truth without an LLM (`evals/datasets/qa.jsonl`, deterministic): the builder fails
if any gold evidence is missing. Baselines are raised only after a measured improvement, and any decrease is
reported explicitly (baseline v3: MRR 0.731 → 0.730, nDCG 0.772 → 0.771, from replacing a random
tie order with a fixed one).

## Why
Offline, the LLM and Jev are mocks, so only pipeline properties are measurable; those are exactly the
security-critical ones (ACL, PII, actions, silent failure) and they must be exact zeros, not averages.

## Evidence that the gates bite
- M4: a deliberately broken retrieval (dense-only, k=2) failed with 3 regressions; the eval found the contract-id
  bug (contract recall 0.850 → 1.000, MRR 0.497 → 0.894 after the fix).
- M6: two identical runs gave MRR 0.746 and 0.727, exposing non-deterministic fusion.
- M8: the agent suite caught that scripted directives were being blocked by the classifier (9/37 failing).

## Not yet possible (needs keys)
`eval-full` with generation: faithfulness, correctness, Jev vs Gemini judge agreement (κ), Jev τ sweeps, the router
cost-vs-quality curve. The Insights page shows these as empty states rather than mock numbers.

## Alternatives rejected
- *Only LLM-judged metrics*: slow, costly under free-tier limits, and non-deterministic: unsuitable per commit.
- *Average-based security metrics*: one leak in 1,000 is still a leak; hard zeros instead.
