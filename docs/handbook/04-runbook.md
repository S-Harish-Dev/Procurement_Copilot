# 4. Runbook and failure drills

Each entry: **symptom → dashboard → trace → fix**, then what the drill observed (`make chaos`, run
20261030T181632Z, native stack, offline; docs/chaos-drills.md). Dashboards are in Grafana folder *Procurement Copilot*
(`pc-overview`, `pc-llm-cost`, `pc-retrieval`, `pc-security`, `pc-dependencies`); alerts in
`infra/prometheus/alerts.yml`. Traces: Insights → Traces (`/insights/traces/<request_id>`, admin only).

**Common commands**
```bash
scripts/native-stack.sh status                 # every dependency up/DOWN (native stack)
scripts/native-stack.sh start                  # respawn whatever isn't running
scripts/native-stack.sh restart-gateway        # restart the gateway (waits for /readyz)
scripts/native-stack.sh restart-python         # brain + both MCP servers
curl -s localhost:8080/readyz | jq          # per-dependency readiness, index_version
make chaos                                  # re-run all resilience drills (stops real processes)
# admin token: TOKEN=$(curl -s -XPOST localhost:8080/v1/auth/login -d '{"user_id":"olivia.grant"}' \
#   -H 'content-type: application/json' | jq -r .token)
curl -s -XPOST localhost:8080/admin/cache/flush -H "authorization: Bearer $TOKEN"      # audited
curl -s -XPOST localhost:8080/admin/users/<id>/reset-strikes -H "authorization: Bearer $TOKEN"  # audited
```
Docker: `docker compose -f infra/docker-compose.yml ps|logs|restart <service>`; `make chaos MODE=compose`.

---

## Jev unavailable (timeouts, 429/529, breaker open, budget ≥ 95%)
- **Symptom:** answers still arrive but routing is coarse (everything on the expensive tier); admins see `degraded:
  ["jev"]` in diagnostics.
- **Dashboard:** `pc-dependencies` → *Jev degraded*; `pc-llm-cost` → *Jev requests by kind and result*. Alert
  **JevDegraded** (5 min).
- **Trace:** `jev.route` / `jev.gate` spans short (fast failure) and no gate span when the breaker is open.
- **Fix:** check the Jev endpoint and key; check budget (`jev_budget_spent_usd`); the breaker retries after 30 s by itself.
- **Drill:** answered; diagnostics `["jev"]`; gauge = 1; recovered to a non-degraded answer in **17.1 s** (breaker cooldown).

## One LLM provider down
- **Symptom:** nothing visible to users; slightly higher latency.
- **Dashboard:** `pc-llm-cost` → *Fallbacks* and *LLM requests by provider and result*; alert **LlmErrorRate** (> 20%
  failing for 5 min).
- **Trace:** diagnostics `provider` is the fallback (e.g. `mock-secondary/mock-small`).
- **Fix:** check the provider's key/quota; 401/403 trips the breaker immediately, so a fixed key needs ≤ 30 s.
- **Drill:** answered by the secondary provider, `llm_fallback_total` 0 → 1, recovered in **0.7 s**.

## All LLM providers down
- **Symptom:** users get "The assistant is temporarily unavailable. Please try again shortly." for new questions;
  common questions still answered from cache.
- **Dashboard:** `pc-llm-cost` → *LLM requests* (all non-ok); `pc-overview` → *Answers by source* shifts to `cache`.
- **Fix:** restore any one provider (keys, quotas); free-tier daily limits reset per provider.
- **Drill:** fresh question → the professional message; cached answer still served; recovered in **2.2 s**.

## Valkey down
- **Symptom:** none for users; cache hit rate drops to L1 only; rate limiting per process.
- **Dashboard:** `pc-dependencies` → *Dependency up* (`valkey`); `pc-retrieval` → *Answer-cache hits by layer* (no
  `l2`). Alert **DependencyDown**.
- **Fix:** restart Valkey (`scripts/native-stack.sh start`); the gateway reconnects in the background.
- **Drill:** answered without Valkey; requests still admitted (in-process buckets); recovered in **1.6 s**.

## Qdrant (or the embedder) down
- **Symptom:** every uncached question gets "temporarily unavailable"; requests, approvals and budgets keep working.
- **Dashboard:** `pc-dependencies` → *HTTP latency by route* (chat fast-failing); `/readyz` `qdrant: false`.
- **Trace:** `retrieve.hybrid` missing; diagnostics `degraded: ["qdrant"]`.
- **Fix:** restart Qdrant; check disk. If the collection is gone: `make ingest` (loads the bundle, no embedding).
- **Drill:** Q&A "temporarily unavailable"; `list_my_requests` still returned 20 items; recovered in **0.7 s**.

## OPA down
- **Symptom:** answers only from public documents (for every role); approvals and purchase requests denied.
- **Dashboard:** `pc-security` → *Actions by tool and result* (denied rising); diagnostics `degraded: ["opa"]`.
- **Fix:** restart OPA; check `policies/*.rego` compile (`make opa-test`). Natively OPA runs with `--watch`, so a
  broken policy edit is a common cause.
- **Drill:** public Q&A continued; Finance's scope fell back to `["public"]`; writes denied; recovered in **2.2 s**.

## Postgres down
- **Symptom:** "The request couldn't be completed." on actions; Q&A works; new logins fail.
- **Dashboard:** `pc-dependencies` → *Dependency up* (`postgres`); alert **DependencyDown**.
- **Fix:** `scripts/native-stack.sh start` (runs `pg_ctl start`); check `.native/postgres.log` / disk.
- **Drill:** actions refused with a clear message (no stack trace); Q&A answered; recovered in **0.8 s**.

## Brain down
- **Symptom:** action requests ("approve this", "show my requests") get "temporarily unavailable"; Q&A fine.
- **Dashboard:** `pc-security` → *Actions by tool and result* (`agent · brain_unavailable`).
- **Fix:** `scripts/native-stack.sh restart-python`; check `.native/brain.log`.
- **Drill:** action intent → "temporarily unavailable"; Q&A answered; recovered in **1.5 s**.

## Classifier skipped (guard layer 4 degraded)
- **Symptom:** none for users. Alert **ClassifierSkipped**; `classifier_skipped_total{reason}` rising.
- **Meaning by reason:** `timeout` = the 4 × 4 pool can't keep up (1.5 s timeout); `not_loaded` = model missing or
  still loading; `error` = inference failure. Affected answers are marked degraded and never cached.
- **Dashboard:** `pc-retrieval` → *Stage latency p95*; `/readyz` `classifier`.
- **Fix:** raise `CLASSIFIER_INSTANCES` / `CLASSIFIER_THREADS` (keep instances × threads within the cores you own);
  check `CLASSIFIER_DIR` (`make classifier-model`). Seen once: ~200 `not_loaded` skips at one time point, caused by
  readiness not waiting for the model (fixed in M9; the next load test had 0).

## Suspected cache leak (someone saw an answer they shouldn't)
1. Flush immediately: `POST /admin/cache/flush` (L1, Valkey L2, semantic; audited).
2. Find the request in Insights → Traces: `cache_layer` and `scope_hash` in the diagnostics.
3. Re-run `make cache-check` (hard gate `cross_scope_serves = 0`) and `make eval-fast` (ACL leak gate).
4. If the cause is a policy change, `scope_hash` changes with the grants; if it's a document reclassification, load a
   new index version (all keys change).

## Latency SLO breach
- **Alert:** **ChatLatencySLO** (chat p95 > 8 s for 10 min).
- **Dashboard:** `pc-overview` → *Chat latency p50/p95*; `pc-retrieval` → *Stage latency p95* tells which stage.
- **Typical causes measured here:** query embedding queueing (retrieval at 100 VUs: 53 RPS, p95 2.0 s); thread
  oversubscription (fixed: 4 instances × 4 threads); free-tier provider latency; host load (load average up to 35).
- **Fix:** scale embedder instances on dedicated cores, check cache hit rate, check provider fallbacks.

## Guard block spike
- **Alert:** **GuardBlockSpike** (> 20 blocks in 10 min).
- **Dashboard:** `pc-security` → *Guard blocks by layer*, *Silent templates served*.
- **Triage:** one user (strikes) or many (campaign or a false-positive wave after a model/pattern change)?
  `security_events` holds masked input, layer and score.
- **Fix:** campaign → leave strikes in place; false positives → reset strikes for affected users
  (`POST /admin/users/<id>/reset-strikes`), compare with the `tau_classifier` experiment, adjust thresholds via env
  (`TAU_CLS`, `TAU_CLS_VOTE`, `CLS_MIN_WORDS`) and re-run `make redteam` before changing anything permanently.
