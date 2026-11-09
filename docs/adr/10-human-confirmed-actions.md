# ADR-10: Write actions are proposals until a human confirms; one-time HMAC tokens; OBO identity

- Status: accepted (2026-10-30)

## Decision
1. Write tools are two-phase. Without `confirmation_token` they validate exactly as execution would (OPA,
   budget, vendor status, state machine) and store a *proposal* (`action_confirmations`: action, canonical
   payload text, sha256, summary, 5-minute expiry). Nothing changes.
2. Only `POST /v1/actions/{id}/confirm` mints a token: gateway code reached by a human click, never by the
   LLM. In one transaction it locks the row and checks: same user, `proposed`, unexpired, unused, stored canonical
   payload still hashes to `payload_hash`. Token = `base64url(json grant).base64url(HMAC-SHA256)` binding
   proposal id + user + payload hash + 5-min expiry + nonce; only its hash is stored.
3. The brain executes without an LLM (`ExecuteConfirmed`): it reloads the *stored canonical text* and calls
   the tool with the token.
4. The MCP server verifies again (signature, expiry, user = OBO subject, payload hash = hash of the call's
   arguments, `token_hash` matches the row) and marks it used in the same transaction as the action, so use
   is atomic with execution. A replay returns the stored result (`replayed: true`); the proposal id is also the
   PR's idempotency key.
5. Identity everywhere is an OBO JWT (RS256, `aud=mcp`, 60 s) minted per request; MCP servers verify it with
   the gateway's public key and re-ask OPA; headers alone are never trusted (the MCP SDK says the same).
6. Defence in depth below: Postgres triggers forbid self-approval and illegal transitions; `UNIQUE(pr_id,
   approver_id)` enforces one level per person; `budgets` CHECK prevents over-commit.

## Why hash stored text, not JSONB?
JSONB re-serialises numbers and key order differently per language; hashing the exact canonical text the user
was shown makes the check independent of Rust/Python serialisation.

## Evidence (offline, real stack; `make agent-tasks`, `tests/test_actions.py`)
37/37 agent tasks, `unauthorized_actions = 0`; integration tests: tampered payload with a genuine token rejected;
token replay returns the same PR (no second order); another user's identity rejected; token signed with a guessed
key rejected; double confirm → 409; confirming someone else's proposal → 404 (indistinguishable from missing).

## Alternatives
- *Let the LLM call writes directly with a "confirm" argument*: the model could set it. Rejected.
- *Session-based confirmation (UI state only)*: not verifiable by the MCP server. Rejected.
