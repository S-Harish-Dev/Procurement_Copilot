#!/usr/bin/env bash
# Load tests on the native stack: each k6 scenario runs against a gateway restarted with
# load-test settings (per-user rate limits lifted; caches off for the end-to-end path), then the
# normal gateway is restored. Summaries land in evals/loadtest/<run>/ and the report is built by
# `python -m evals.loadtest_report`. Docker users: export the same env in .env and run k6 directly.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
K6="${K6:-$HOME/.local/bin/k6}"
RUN="$(date -u +%Y%m%dT%H%M%SZ)"
OUT="$REPO/evals/loadtest/$RUN"
DURATION="${DURATION:-60s}"
mkdir -p "$OUT"

restart_gateway() { # extra env assignments...
  env "$@" RATE_LIMIT_BURST=1000000000 RATE_LIMIT_PER_SEC=1000000000 \
    "$REPO/scripts/native-stack.sh" restart-gateway >/dev/null
}

scenario() { # name, vus, gateway env...
  local name=$1 vus=$2; shift 2
  restart_gateway "$@"
  echo "== $name ($vus VUs, $DURATION) =="
  "$K6" run --quiet --summary-export "$OUT/$name.json" -e VUS="$vus" -e DURATION="$DURATION" \
    "$REPO/infra/k6/$name.js" | tail -n 25 || echo "$name: thresholds failed (see summary)"
}

{
  echo "{\"run\": \"$RUN\", \"host\": \"$(hostname -s)\", \"cores\": $(nproc),"
  echo " \"loadavg_before\": \"$(cut -d' ' -f1-3 /proc/loadavg)\", \"duration\": \"$DURATION\","
  echo " \"git_sha\": \"$(git -C "$REPO" rev-parse --short HEAD)\"}"
} >"$OUT/meta.json"

scenario cache_hit "${CACHE_VUS:-64}" CACHE_ENABLED=true
scenario retrieval "${RETRIEVAL_VUS:-100}" CACHE_ENABLED=true
scenario e2e_mock "${E2E_VUS:-32}" CACHE_ENABLED=false

"$REPO/scripts/native-stack.sh" restart-gateway >/dev/null   # back to normal settings
echo "summaries in $OUT"
