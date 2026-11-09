#!/usr/bin/env bash
# Advanced, no Docker: run the infrastructure as plain user processes. Expects the binaries named
# below (paths under ~/.local/opt); the Docker Compose stack is the default way to run everything.
# Same versions as infra/docker-compose.yml: Postgres 18.6, Valkey 9.1.3, Qdrant 1.20.0, OPA 1.22,
# plus two mock-llm instances (ports 8090/8091) for offline runs, load tests and fallback drills.
# On a laptop use `make up` (Docker Compose) instead.
#
#   scripts/native-stack.sh start|stop|status
#
# Everything binds to 127.0.0.1; Postgres (SCRAM) and Valkey (requirepass) use the passwords from
# .env because other users of a shared machine can reach localhost.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
STATE="$REPO/.native"
BIN="$HOME/.local/opt/pc-stack/bin"
QDRANT="$HOME/.local/opt/qdrant-1.20.0/qdrant"
OPA="$HOME/.local/bin/opa"
MAILPIT="$HOME/.local/opt/mailpit-1.32.0/mailpit"
PROMETHEUS="$HOME/.local/opt/prometheus/prometheus-3.15.1.linux-amd64/prometheus"
GRAFANA_HOME="$HOME/.local/opt/grafana/grafana-13.3.0"
PGSOCK="/run/user/$(id -u)/pc-pg"   # Unix sockets are limited to 107 bytes; the repo path is long
MOCK="$REPO/gateway/target/release/mock-llm"
GATEWAY="$REPO/gateway/target/release/gateway"

set -a; source "$REPO/.env"; set +a
mkdir -p "$STATE"

running() { [[ -f "$STATE/$1.pid" ]] && kill -0 "$(cat "$STATE/$1.pid")" 2>/dev/null; }

spawn() { # name, then command...
  local name=$1; shift
  if running "$name"; then echo "$name already running"; return; fi
  setsid nohup "$@" >"$STATE/$name.log" 2>&1 < /dev/null &
  echo $! >"$STATE/$name.pid"
  echo "started $name (pid $!)"
}

start() {
  mkdir -p "$PGSOCK"; chmod 700 "$PGSOCK"
  if [[ ! -d "$STATE/pg" ]]; then
    "$BIN/initdb" -D "$STATE/pg" -U "$POSTGRES_USER" --auth=scram-sha-256 \
      --pwfile=<(printf '%s\n' "$POSTGRES_PASSWORD") -E UTF8 >"$STATE/initdb.log"
  fi
  if ! "$BIN/pg_ctl" -D "$STATE/pg" status >/dev/null 2>&1; then
    "$BIN/pg_ctl" -D "$STATE/pg" -l "$STATE/postgres.log" -w start \
      -o "-c listen_addresses=127.0.0.1 -p 5432 -c unix_socket_directories=$PGSOCK" >/dev/null
    echo "started postgres"
  fi
  PGPASSWORD="$POSTGRES_PASSWORD" "$BIN/psql" -h 127.0.0.1 -U "$POSTGRES_USER" -d postgres -Atqc \
    "SELECT 1 FROM pg_database WHERE datname='$POSTGRES_DB'" | grep -q 1 \
    || PGPASSWORD="$POSTGRES_PASSWORD" "$BIN/createdb" -h 127.0.0.1 -U "$POSTGRES_USER" "$POSTGRES_DB"

  spawn valkey "$BIN/valkey-server" --bind 127.0.0.1 --port 6379 --requirepass "$VALKEY_PASSWORD" \
    --save "" --appendonly no --maxmemory 1gb --maxmemory-policy allkeys-lru
  QDRANT__STORAGE__STORAGE_PATH="$STATE/qdrant" QDRANT__SERVICE__HOST=127.0.0.1 \
    QDRANT__TELEMETRY_DISABLED=true spawn qdrant "$QDRANT"
  # --watch: policy and data edits apply without a restart (dev only; compose bakes them in).
  spawn opa "$OPA" run --server --watch --addr 127.0.0.1:8181 --log-format json \
    --set decision_logs.console=true "$REPO/policies/authz.rego" "$REPO/policies/data.json"
  spawn prometheus "$PROMETHEUS" --config.file="$REPO/infra/prometheus/prometheus.native.yml" \
    --storage.tsdb.path="$STATE/prometheus" --storage.tsdb.retention.time=15d --web.listen-address=127.0.0.1:9090
  # Grafana with the repo's provisioning (datasource + generated dashboards); localhost only,
  # anonymous viewers so the dashboards open without a login during development.
  [[ -x "$GRAFANA_HOME/bin/grafana" ]] && GF_PATHS_DATA="$STATE/grafana" GF_PATHS_LOGS="$STATE/grafana" \
    GF_PATHS_PROVISIONING="$REPO/infra/grafana/provisioning" GF_SERVER_HTTP_ADDR=127.0.0.1 \
    GF_SERVER_HTTP_PORT=3000 GF_AUTH_ANONYMOUS_ENABLED=true GF_AUTH_ANONYMOUS_ORG_ROLE=Viewer \
    GF_ANALYTICS_REPORTING_ENABLED=false GF_ANALYTICS_CHECK_FOR_UPDATES=false \
    PROMETHEUS_URL=http://127.0.0.1:9090 DASHBOARDS_DIR="$REPO/infra/grafana/dashboards" \
    spawn grafana "$GRAFANA_HOME/bin/grafana" server --homepath "$GRAFANA_HOME"
  spawn mailpit "$MAILPIT" --listen 127.0.0.1:8025 --smtp 127.0.0.1:1025 --database "$STATE/mailpit.db"
  if [[ -x "$MOCK" ]]; then
    MOCK_PORT=8090 spawn mock-llm "$MOCK"
    MOCK_PORT=8091 spawn mock-llm-2 "$MOCK"
    # Jev gets its own instance so a Jev outage can be drilled without taking an LLM down too.
    MOCK_PORT=8092 spawn mock-jev "$MOCK"
  else
    echo "mock-llm not built: cargo build --release -p mock-llm (in gateway/)"
  fi
  start_python
  sleep 2
  start_gateway
  status
}

# Brain (gRPC :50051) and the two MCP servers (:8101, :8102), from the project venv.
start_python() {
  local py="$REPO/brain/.venv/bin/python"
  [[ -x "$py" ]] || { echo "brain venv missing: cd brain && uv sync"; return; }
  ( cd "$REPO/brain" && MCP_PORT=8101 spawn mcp-procurement "$py" -m mcp_servers.procurement.server )
  ( cd "$REPO/brain" && MCP_PORT=8102 spawn mcp-notify "$py" -m mcp_servers.notify.server )
  ( cd "$REPO/brain" && spawn brain "$py" -m agent.server )
}

# Offline unless real keys are present: mock LLM tiers and, without a TypeSafe key, mock Jev.
start_gateway() {
  [[ -x "$GATEWAY" ]] || { echo "gateway not built: make gateway-build"; return; }
  local providers="gateway/providers.toml"
  [[ -z "${GROQ_API_KEY}${GEMINI_API_KEY}${OPENROUTER_API_KEY}" ]] && providers="gateway/providers.mock.toml"
  local jev_url="${JEV_BASE_URL}" jev_key="${TYPESAFE_API_KEY}"
  [[ -z "$jev_key" ]] && { jev_url="http://127.0.0.1:8092"; jev_key="mock"; }
  ( cd "$REPO" && source scripts/env.sh && REPO_ROOT=. PROVIDERS_TOML="$providers" \
      JEV_BASE_URL="$jev_url" TYPESAFE_API_KEY="$jev_key" EVAL_ENDPOINTS=true \
      GATEWAY_BIND=127.0.0.1:8080 GATEWAY_METRICS_BIND=127.0.0.1:9464 RUST_LOG="${RUST_LOG:-info}" \
      spawn gateway "$GATEWAY" )
  for _ in $(seq 1 90); do curl -sf http://127.0.0.1:8080/readyz >/dev/null && break; sleep 1; done
  echo "gateway providers=$providers jev=$jev_url"
}

stop() {
  for name in gateway mcp-procurement mcp-notify brain mock-llm mock-llm-2 mock-jev mailpit grafana prometheus opa qdrant valkey; do
    if running "$name"; then kill "$(cat "$STATE/$name.pid")" && echo "stopped $name"; fi
    rm -f "$STATE/$name.pid"
  done
  "$BIN/pg_ctl" -D "$STATE/pg" stop -m fast >/dev/null 2>&1 && echo "stopped postgres" || true
}

status() {
  check() { printf '%-10s %s\n' "$1" "$(eval "timeout 5 $2" >/dev/null 2>&1 && echo up || echo DOWN)"; }
  check postgres "env PGPASSWORD='$POSTGRES_PASSWORD' '$BIN/psql' -h 127.0.0.1 -U '$POSTGRES_USER' -d '$POSTGRES_DB' -Atqc 'select 1'"
  check valkey "'$BIN/valkey-cli' -a '$VALKEY_PASSWORD' --no-auth-warning ping | grep -q PONG"
  check qdrant "curl -sf http://127.0.0.1:6333/readyz"
  check opa "curl -sf http://127.0.0.1:8181/health"
  check mailpit "curl -sf http://127.0.0.1:8025/livez"
  check prometheus "curl -sf http://127.0.0.1:9090/-/ready"
  check grafana "curl -sf http://127.0.0.1:3000/api/health"
  check mock-llm "curl -sf http://127.0.0.1:8090/healthz"
  check mock-llm-2 "curl -sf http://127.0.0.1:8091/healthz"
  check mock-jev "curl -sf http://127.0.0.1:8092/healthz"
  # MCP endpoints keep GET streams open, so probe the TCP port only (with a timeout).
  check mcp-proc "timeout 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/8101'"
  check mcp-notify "timeout 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/8102'"
  check brain "timeout 2 bash -c 'exec 3<>/dev/tcp/127.0.0.1/50051'"
  check gateway "curl -sf http://127.0.0.1:8080/readyz"
}

case "${1:-status}" in
  start) start ;;
  restart-gateway) running gateway && kill "$(cat "$STATE/gateway.pid")"; rm -f "$STATE/gateway.pid"; sleep 1; start_gateway ;;
  restart-python) for n in brain mcp-procurement mcp-notify; do running $n && kill "$(cat "$STATE/$n.pid")"; rm -f "$STATE/$n.pid"; done; sleep 1; start_python ;;
  stop) stop ;;
  status) status ;;
  *) echo "usage: $0 start|stop|status" >&2; exit 2 ;;
esac
