# ADR-11: Scope-keyed caches: moka L1 → Valkey L2 → semantic (Qdrant)

- Status: accepted (2026-10-30)

## Decision
One `cache` crate, six caches, all keyed so a cached result can only reach a request that would
have produced it:

| cache | key / filter | TTL | where |
|---|---|---|---|
| answers | `sha256(normalized masked query, scope_hash, index_version, prompt_version, tier)` | 24 h | L1 + L2 |
| semantic | cosine ≥ 0.90 (M10 sweep; was 0.95) and exact `scope_hash`, `index_version`, `prompt_version`, tier, `key_terms` | 24 h | Qdrant `semcache` |
| retrieval | exact masked query, scope, index, hybrid params | 1 h | L1 + L2 |
| embeddings | model, exact masked query | 7 d | L1 + L2 |
| Jev route | masked query + recent turns, router question version | 24 h | L1 + L2 |
| Jev gate | masked query, chunk id, index, gate question version (per passage) | 24 h | L1 + L2 |

Order on the request path: guard and router → OPA scope → exact answer cache → embed → **semantic
cache** → retrieval → gate → LLM. Writes happen only after an answer streamed completely through the
output guard, and only when nothing was degraded.

Keys are SHA-256 over length-prefixed fields (user text can't forge a separator).

## Why these rules
- Scope in every answer-bearing key. `scope_hash = sha256(role | canonical grants)`: two users with
  identical visibility share entries, and anyone else can't reach them. Verified by `make cache-check`:
  the same question asked by the document owner and by another department, 10 probes, 0 cross-scope serves.
- Masked text is cached, unmasked per request. The LLM only ever saw masked text, so the cached answer is
  a function of the masked query. Serving it re-applies the *current* user's own unmasking rights.
- Not cached: blocked requests, abstentions, agent/action responses, and answers produced in degraded
  mode, which would otherwise outlive the outage by 24 h.
- Cache reads are allowed in degraded mode (Jev or LLM down), except with a fail-closed OPA scope:
  by design, cached answers keep serving during an LLM outage.
- Semantic `key_terms`. Numbers and identifiers embed weakly, so "approve under $1,000" and "$10,000" are near
  neighbours. They must match exactly, which is a hard filter rather than a similarity score.
- Jev gate all-or-nothing. Scores come from one batched call; mixing cached and fresh scores would
  change the batch Jev sees, so a partial hit re-asks for the whole batch.
- Embeddings and retrieval key on the exact text (not the normalized one): a hit is bit-identical
  to recomputing, so caching can't change eval results.

## Invalidation
- `index_version` and `prompt_version` are part of every relevant key: a bump makes old entries unreachable.
- The gateway polls the active version (`INDEX_POLL_SECS`, 10 s). On a change it clears its L1 and deletes
  `semcache` points of other versions; L2 entries expire by TTL. `cache-check` flips the active version
  and back: miss after the bump, an L2 hit again after restoring (nothing was overwritten).
- `POST /admin/cache/flush` (audited) clears all layers: SCAN + UNLINK in Valkey, never KEYS.

## Measured (offline, mock LLM)
Exact hit: 23 ms vs 902 ms for the same question computed. Same-scope sharing (two Finance users) and
case/punctuation normalization both hit L1. Semantic: 0 of 10 paraphrase pairs hit at 0.95. Starting strict (0.95) is too strict for nomic paraphrases; threshold tuning on the paraphrase set is M10.

## Alternatives rejected
- *Cache keyed by user id*: safe but nearly useless (no sharing).
- *Cache keyed by role only*: wrong. Two employees of different departments share a role but not a scope.
- *Redis-only (no L1)*: a network round trip per lookup; moka hits cost microseconds.
- *Semantic cache without hard filters (threshold only)*: one paraphrase across scopes would be a leak.
