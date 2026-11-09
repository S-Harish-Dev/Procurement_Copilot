# ADR-07: Gate request shape: one batched request vs one request per passage

- Status: proposed (spike pending a TypeSafe API key) (2026-10-30)

## Question
TypeSafe's docs recommend putting all questions in one request ("adding questions barely changes the response
time"). Their cookbook pattern for relevance puts query + passage in `state`, one request per passage. We batch
20 passages × 2 questions into one request with the passage inside each question's `instructions`.

## Plan (M3 spike, run as soon as a key exists)
On 50 eval queries, run both shapes on the same 20 RRF candidates and compare:
agreement (Cohen's κ on keep/drop at τ), recall/precision vs gold chunks, p50/p95 latency, input tokens and cost.
Keep batched unless quality drops by more than 2 points. Results are recorded here.

## Current state
The gateway implements the batched shape (`jev::QuestionSets::gate_questions`). The per-passage shape is a small
variant of `JevClient::gate` to add for the spike. Offline runs use the mock gate, which says nothing about Jev.
