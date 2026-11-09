# ADR-06: Layered input guard with an ensemble verdict, silent failure, and strikes

- Status: accepted (2026-10-30)

## Decision
Every chat message passes, in order:
1. Normalize (`guard::normalize`): NFKC, strip zero-width/bidi/control characters, map common Cyrillic and
   Greek look-alikes to Latin, collapse runs of spaces, cap length. Later layers see the normalized text.
   Leetspeak and letter-spaced text are *not* folded; those are left to the classifier and Jev.
2. Regex (`prompts/injection_patterns_v1.json`, shared with ingestion), run on the normalized text and on any
   base64/hex blob decoded from it: `high` patterns block on their own, `low` patterns only count as a second
   signal.
3. Jev router (`inj` noul): blocks at ≥ τ_inj = 0.8.
4. Local classifier (ONNX in the gateway, `crates/classifier`): blocks alone at ≥ τ_cls = 0.9, and at
   ≥ τ_cls_vote = 0.5 when there is a second signal (any regex hit, Jev ≥ 0.5, or Jev unavailable).
   *Amended in M8:* messages under `CLS_MIN_WORDS` = 6 words need a real second signal (regex or Jev).
   The classifier alone can't block them, and "Jev unavailable" doesn't count. It scored terse commands
   erratically ("approve this" 1.000, "approve it" 0.000) and silently blocked 9 of 22 ordinary approval
   commands. With the rule: 0/22 blocked, red-team guard detection unchanged (88/101), benign FP unchanged.

Layers 3 and 4 run concurrently (`tokio::join!`). The first layer that fires is recorded (`block_layer`) for the
admin diagnostics, never shown to the user.

A blocked message gets the same response as a genuine no-answer: identical template, identical SSE event
sequence (`meta, delta, done`), status `no_answer`, padded to a delay drawn from recorded real no-answer latency.
Each block is a strike in Valkey: throttle at 3, lock at 10 (24 h); a locked user gets the same template for
everything.

## Why an ensemble and not a single detector
- Regex is precise on known phrasings and blind to paraphrase; the classifier is the reverse and has real false
  positives on procurement language ("approve", "override", "ignore the rule"); Jev is remote and can be down.
- The vote rule lets a mid-confidence classifier score block only when something independent agrees, and treats
  "Jev unavailable" as agreement, so an outage fails closed rather than open.

## Why silent failure
An attacker who can tell "blocked" from "no answer" can search for the boundary. Identical text, events and
timing remove that oracle. The red team checks it directly: template match, event sequence, and the share of
blocked responses whose latency falls inside the genuine no-answer band.

## Classifier choice
Preferred: Prompt Guard 2 (86M, gated, no ONNX). Until `HF_TOKEN` exists, the ungated ProtectAI DeBERTa-v3
injection classifier stands in. The loader finds the positive label from `config.json` (`id2label`), so the swap is
a directory change. Inputs longer than 512 tokens are scored over sliding windows (stride 128) and the
maximum is kept, so an instruction buried deep in a long message isn't diluted.

Quantization: only MatMul weights, per channel, and only if the int8 copy agrees with fp32 on labelled probes.
Naive full dynamic int8 scored every probe ≈ 0.02 and was rejected.

## Measured (offline: mock LLM and mock Jev, real gateway)
`make redteam`: 144 attacks in 8 categories plus 150 benign questions (10 deliberately "tricky"); results are
written to `evals/reports/`. Every hard gate is 0: no canary or system-prompt leak, no cross-role content, no unmasked
PII, no unconfirmed action, every HTTP abuse check fails closed, and no template mismatch. Benign FPR is 0.7%:
one real classifier false positive. `make eval-fast` tracks `guard_detection` and `guard_benign_fpr` on every
commit through `POST /admin/eval/guard` (regex and classifier only, so the number is deterministic).

## Alternatives rejected
- *Classifier only*: its false positives on domain language would block ordinary questions.
- *Telling the user they were blocked*: a better UX, but it gives attackers a free oracle. Admins see the reason
  code and can reset strikes instead.
- *Running the classifier in the Python brain*: an extra gRPC hop on every request, and the guard should sit at
  the edge, before any agent code runs.
