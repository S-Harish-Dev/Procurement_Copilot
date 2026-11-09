# Procurement Copilot — developer entry points.
# The repo path contains a space, so recipes use relative paths and never $(CURDIR) in targets.

SHELL := bash
.SHELLFLAGS := -euo pipefail -c
.DEFAULT_GOAL := help

# User-local toolchain (Rust, uv, opa, k6, protoc). Directories that don't exist on PATH are skipped.
export PATH := $(HOME)/.cargo/bin:$(HOME)/.local/bin:$(PATH)
# Keep uv on the project venv even if another env is active.
unexport VIRTUAL_ENV

# The gateway loads ONNX Runtime dynamically (fastembed `ort-load-dynamic`). Locally, reuse the
# library shipped in the Python onnxruntime wheel; Docker images set ORT_DYLIB_PATH themselves.
export ORT_DYLIB_PATH ?= $(shell ls "$(CURDIR)"/brain/.venv/lib/python3.14/site-packages/onnxruntime/capi/libonnxruntime.so.* 2>/dev/null | head -1)
export FASTEMBED_CACHE_DIR ?= $(CURDIR)/models/fastembed

COMPOSE := docker compose --env-file .env -f infra/docker-compose.yml
UV := cd brain && uv run
# Heavy deps (docling, torch, spaCy, Presidio) live in the optional `ingest` group: only the build
# machine needs them. Laptops load the precomputed index bundle instead.
UV_INGEST := cd brain && uv run --group ingest

.PHONY: help env keys up down ps logs migrate download synth seed ingest smoke build-index ablation gateway-test parity gateway-build native-stack eval-fast eval-baseline eval-datasets agent-tasks redteam redteam-cases classifier-model cache-check chaos loadtest experiments docs-pdf \
        test test-py opa-test lint fmt ci web-install web-dev web-build web-start web-lint web-verify dashboards

help: ## list targets
	@grep -hE '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-16s %s\n", $$1, $$2}'

env: ## create .env from .env.example with fresh random secrets (never overwrites)
	python3 scripts/make_env.py

keys: ## create the dev RS256 JWT key pair in infra/keys (gitignored; never overwrites)
	@mkdir -p infra/keys
	@test -f infra/keys/jwt-private.pem || { \
	  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out infra/keys/jwt-private.pem 2>/dev/null; \
	  openssl pkey -in infra/keys/jwt-private.pem -pubout -out infra/keys/jwt-public.pem; \
	  chmod 600 infra/keys/jwt-private.pem; echo "wrote infra/keys/jwt-{private,public}.pem"; }

up: env keys ## start infrastructure and wait until it is ready
	$(COMPOSE) up -d --wait postgres qdrant valkey mailpit otel-lgtm
	$(COMPOSE) up -d opa
	@for i in $$(seq 1 30); do curl -sf http://127.0.0.1:$${OPA_PORT:-8181}/health >/dev/null && break; sleep 1; done
	@curl -sf http://127.0.0.1:$${OPA_PORT:-8181}/health >/dev/null && echo "stack ready"

down: ## stop the stack (keeps volumes)
	$(COMPOSE) down

ps: ## show service status
	$(COMPOSE) ps

logs: ## follow logs (S=service to filter)
	$(COMPOSE) logs -f $(S)

migrate: ## apply db/migrations
	$(UV) python -m common.migrate

download: ## fetch the public corpus (checksums in data/manifest.json)
	$(UV) python -m ingestion.download

synth: ## regenerate deterministic synthetic Acme data into data/synthetic
	$(UV) python -m synthetic.generate

seed: migrate ## load synthetic Acme data into Postgres
	$(UV) python -m synthetic.seed

ingest: ## load the precomputed index bundle into Postgres + Qdrant (light; no models)
	$(UV) python -m ingestion.load

smoke: ## retrieval smoke test against the loaded index (hybrid + ACL, 6 real questions)
	$(UV) python -m ingestion.smoke

build-index: download ## HEAVY, build machine only: parse, chunk, mask, scan, embed -> data/index_bundle
	$(UV_INGEST) python -m ingestion.pipeline

ablation: ## HEAVY, build machine only: embed the corpus with the M10 alternative embedders
	$(UV_INGEST) python -m ingestion.ablation

opa-test: ## policy unit tests
	opa check --strict policies/
	opa test policies/ -v

test-py: ## python unit tests (ingestion-model tests skip when the ingest group isn't installed)
	$(UV) pytest -q

test: opa-test test-py ## all unit tests

eval-fast: ## retrieval metrics + hard security gates vs baseline (needs the running stack); <1 min
	$(UV) python -m evals.fast

eval-baseline: ## record the current eval-fast run as the new baseline (only after a reviewed improvement)
	$(UV) python -m evals.fast --update-baseline --label "$(LABEL)"

agent-tasks: ## agent task suite (37 tasks) through gateway -> brain -> MCP; hard gate: unauthorized actions = 0
	$(UV) python -m evals.agent_tasks run

redteam: ## red-team suite (144 attacks + 150 benign) through the gateway; hard gates must be 0
	$(UV) python -m evals.redteam

dashboards: ## regenerate the Grafana dashboards JSON (infra/grafana/dashboards)
	python3 infra/grafana/build_dashboards.py

docs-pdf: ## M11: docs/Procurement-Copilot-Handbook.pdf offline (handbook + ADRs + Mermaid; CHROME_PATH or Playwright Chromium)
	cd docs/handbook/build && npm ci --silent && \
	  CHROME_PATH="$${CHROME_PATH:-$$(ls -d $$HOME/.cache/ms-playwright/chromium_headless_shell-*/chrome-headless-shell-linux64/chrome-headless-shell 2>/dev/null | tail -1)}" \
	  node build.mjs

experiments: ## M10: ablations + threshold sweeps -> evals/experiments/*.json (Insights → Experiments)
	$(UV) python -m evals.experiments all

chaos: ## M9: drill every row of the resilience matrix (stops real processes; MODE=compose for docker)
	$(UV) python -m evals.chaos --mode $(or $(MODE),native)

loadtest: ## M9: k6 cache-hit / retrieval / e2e (mock LLM) scenarios, then the report in docs/loadtest.md
	scripts/loadtest.sh
	$(UV) python -m evals.loadtest_report

cache-check: ## cache acceptance (M7): scope isolation (hard gate), sharing, semantic, index-bump invalidation
	$(UV) python -m evals.cache_check

redteam-cases: ## regenerate evals/redteam/{cases,benign}.jsonl (deterministic, seed 7)
	$(UV) python -m evals.redteam_cases

classifier-model: ## build machine: download the injection classifier stand-in and its validated int8 copy
	$(UV) python -m ingestion.classifier_models standin
	$(UV) --group ingest python -m ingestion.classifier_models quantize

eval-datasets: ## rebuild evals/datasets/qa.jsonl from ground truth (deterministic)
	$(UV) python -m evals.build_datasets

gateway-test: ## Rust: fmt check, clippy -D warnings, unit tests
	cd gateway && cargo fmt --all --check && cargo clippy --workspace --all-targets -- -D warnings && cargo test --workspace

parity: ## Rust<->Python embedding parity incl. the dense test (downloads nomic once)
	cd gateway && cargo test -p retrieval -- --include-ignored

gateway-build: ## release build of the gateway and mock-llm
	cd gateway && cargo build --release -p api -p mock-llm

native-stack: ## start native Postgres/Valkey/Qdrant/OPA/mock-llm without Docker (advanced; needs the binaries listed in scripts/native-stack.sh)
	./scripts/native-stack.sh start

lint: ## static checks
	opa fmt --fail --list policies/
	cd brain && uv run ruff check . && uv run ruff format --check . && uv run mypy .

fmt: ## auto-format
	opa fmt -w policies/
	cd brain && uv run ruff format . && uv run ruff check --fix .

ci: lint test gateway-test web-lint ## everything CI runs (grows each milestone)

# ---- Web UI (M8) ----------------------------------------------------------------------------------
WEB_ENV := cd web && NEXT_TELEMETRY_DISABLED=1

.PHONY: web-install web-dev web-build web-start web-lint web-verify
web-install: ## web UI: install exact dependency versions (package-lock.json)
	$(WEB_ENV) npm ci
web-dev: ## web UI: dev server on http://localhost:3001 (gateway at NEXT_PUBLIC_GATEWAY_URL, default :8080)
	$(WEB_ENV) npm run dev
web-build: ## web UI: typecheck, lint and production build
	$(WEB_ENV) npm run typecheck && npm run lint && npm run build
web-start: ## web UI: serve the production build on :3001
	$(WEB_ENV) npm run start
web-lint: ## web UI: eslint + tsc
	$(WEB_ENV) npm run lint && npm run typecheck
web-verify: ## web UI: check the live gateway against docs/api.md using the app's own SSE parser
	$(WEB_ENV) npm run verify
