//! Gateway configuration from environment variables (12-factor). Every value has a dev default
//! except secrets, which must come from `.env` / the container environment.

use std::path::PathBuf;
use std::time::Duration;

fn var(key: &str, default: &str) -> String {
    std::env::var(key)
        .ok()
        .filter(|v| !v.is_empty())
        .unwrap_or_else(|| default.to_owned())
}

fn num<T: std::str::FromStr>(key: &str, default: T) -> T {
    std::env::var(key)
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(default)
}

/// Decision thresholds. Starting values from the original plan; tuned on the eval set in M10.
#[derive(Clone, Debug, serde::Serialize)]
pub struct Thresholds {
    /// Router `prompt_injection` probability at or above which the request is silently blocked.
    pub tau_inj: f64,
    /// P(expensive) at or above which the expensive LLM tier is used.
    pub tau_route: f64,
    /// Gate relevance probability needed to keep a passage.
    pub tau_rel: f64,
    /// Gate injection probability at or above which a passage is dropped.
    pub tau_gate_inj: f64,
    /// Max passages sent to the LLM after the gate.
    pub gate_cap: usize,
    /// Passages used when the gate is unavailable (degraded mode): RRF top-k.
    pub degraded_top_k: usize,
    /// Router intent confidence below which we fall back to `policy_qa`.
    pub min_intent_confidence: f64,
    /// Injection classifier score that blocks on its own (layer 4).
    pub tau_cls: f64,
    /// Classifier score that blocks when a second layer agrees (ensemble vote), or alone when Jev is down.
    pub tau_cls_vote: f64,
    /// Below this many words the classifier may not block on its own, and "Jev unavailable" doesn't
    /// count as a second signal: it scores terse commands ("approve this": 1.000, "approve it":
    /// 0.000) erratically. Chosen on the red-team, benign and command sets.
    pub cls_min_words: usize,
}

#[derive(Clone, Debug)]
pub struct Config {
    pub bind_public: String,
    pub bind_internal: String,
    pub bind_metrics: String,
    pub database_url: String,
    pub qdrant_grpc_url: String,
    pub valkey_url: String,
    pub opa_url: String,
    pub brain_url: String,
    pub po_dir: PathBuf,
    pub otlp_endpoint: Option<String>,
    pub jwt_private_key: PathBuf,
    pub jwt_public_key: PathBuf,
    pub confirmation_key_b64: String,
    pub internal_token: String,
    pub providers_toml: PathBuf,
    pub prompts_dir: PathBuf,
    pub assets_dir: PathBuf,
    pub fastembed_cache: PathBuf,
    pub classifier_dir: PathBuf,
    pub embedder_instances: usize,
    /// ONNX intra-op threads per embedder instance (see `QueryEmbedder::load`).
    pub embedder_threads: usize,
    /// Injection-classifier sessions and intra-op threads each (it runs on every chat request).
    pub classifier_instances: usize,
    pub classifier_threads: usize,
    pub jev_api_key: String,
    pub jev_base_url: String,
    pub jev_model: String,
    pub jev_timeout: Duration,
    pub jev_price_in_per_m: f64,
    pub jev_budget_usd: f64,
    pub thresholds: Thresholds,
    pub demo_login: bool,
    pub user_token_ttl_secs: i64,
    pub rate_limit_capacity: f64,
    pub rate_limit_per_sec: f64,
    pub link_allowlist: Vec<String>,
    pub stage_timeout: Duration,
    /// Off only for experiments that must measure the uncached path (CACHE_ENABLED=false).
    pub cache_enabled: bool,
    /// Cosine similarity a paraphrase needs to reuse a cached answer (started strict, tuned in M10).
    pub semcache_threshold: f32,
    /// How often the active index version is re-read; a change invalidates every cache.
    pub index_poll: Duration,
    /// Repository root: Insights reads evals/reports, evals/baselines and evals/experiments here.
    pub repo_root: PathBuf,
    /// Prometheus HTTP API that scrapes this gateway (Insights overview queries it server-side).
    pub prometheus_url: String,
    /// Grafana base URL for "open in Grafana" trace links; unset = no link.
    pub grafana_url: Option<String>,
}

impl Config {
    pub fn from_env() -> Self {
        let repo = PathBuf::from(var("REPO_ROOT", ".."));
        Self {
            bind_public: var("GATEWAY_BIND", "0.0.0.0:8080"),
            // The internal LLM proxy must never be public: brain-only, behind a shared token.
            bind_internal: var("GATEWAY_INTERNAL_BIND", "127.0.0.1:8081"),
            bind_metrics: var("GATEWAY_METRICS_BIND", "0.0.0.0:9464"),
            database_url: var(
                "DATABASE_URL",
                "postgresql://acme:acme-dev-only@localhost:5432/procurement",
            ),
            qdrant_grpc_url: var("QDRANT_GRPC_URL", "http://localhost:6334"),
            valkey_url: var("VALKEY_URL", "redis://localhost:6379"),
            opa_url: var("OPA_URL", "http://localhost:8181"),
            brain_url: var("BRAIN_URL", "http://localhost:50051"),
            po_dir: PathBuf::from(var("PO_DIR", &repo.join("data/po").to_string_lossy())),
            otlp_endpoint: std::env::var("OTEL_EXPORTER_OTLP_ENDPOINT")
                .ok()
                .filter(|v| !v.is_empty()),
            jwt_private_key: PathBuf::from(var(
                "JWT_PRIVATE_KEY",
                &repo.join("infra/keys/jwt-private.pem").to_string_lossy(),
            )),
            jwt_public_key: PathBuf::from(var(
                "JWT_PUBLIC_KEY",
                &repo.join("infra/keys/jwt-public.pem").to_string_lossy(),
            )),
            confirmation_key_b64: var("CONFIRMATION_HMAC_KEY", ""),
            internal_token: var("INTERNAL_API_TOKEN", ""),
            providers_toml: PathBuf::from(var(
                "PROVIDERS_TOML",
                &repo.join("gateway/providers.toml").to_string_lossy(),
            )),
            prompts_dir: PathBuf::from(var("PROMPTS_DIR", &repo.join("prompts").to_string_lossy())),
            assets_dir: PathBuf::from(var(
                "ASSETS_DIR",
                &repo.join("gateway/assets").to_string_lossy(),
            )),
            classifier_dir: PathBuf::from(var(
                "CLASSIFIER_DIR",
                &repo
                    .join("models/classifiers/protectai-deberta-v3-base-injection-v2")
                    .to_string_lossy(),
            )),
            fastembed_cache: PathBuf::from(var(
                "FASTEMBED_CACHE_DIR",
                &repo.join("models/fastembed").to_string_lossy(),
            )),
            embedder_instances: num("EMBEDDER_INSTANCES", 4),
            embedder_threads: num("EMBEDDER_THREADS", 4),
            classifier_instances: num("CLASSIFIER_INSTANCES", 4),
            classifier_threads: num("CLASSIFIER_THREADS", 4),
            jev_api_key: var("TYPESAFE_API_KEY", ""),
            jev_base_url: var("JEV_BASE_URL", "https://api.typesafe.ai"),
            jev_model: var("JEV_MODEL", "jev-latest"),
            jev_timeout: Duration::from_millis(num("JEV_TIMEOUT_MS", 800)),
            jev_price_in_per_m: num("JEV_PRICE_IN_PER_M", 0.042),
            jev_budget_usd: num("JEV_BUDGET_USD", 5.0),
            thresholds: Thresholds {
                tau_inj: num("TAU_INJ", 0.8),
                tau_route: num("TAU_ROUTE", 0.5),
                tau_rel: num("TAU_REL", 0.5),
                tau_gate_inj: num("TAU_GATE_INJ", 0.8),
                gate_cap: num("GATE_CAP", 8),
                degraded_top_k: num("DEGRADED_TOP_K", 6),
                min_intent_confidence: num("MIN_INTENT_CONFIDENCE", 0.5),
                tau_cls: num("TAU_CLS", 0.9),
                tau_cls_vote: num("TAU_CLS_VOTE", 0.5),
                cls_min_words: num("CLS_MIN_WORDS", 6),
            },
            demo_login: var("DEMO_LOGIN", "true") == "true",
            user_token_ttl_secs: num("USER_TOKEN_TTL_SECS", 8 * 3600),
            rate_limit_capacity: num("RATE_LIMIT_BURST", 20.0),
            rate_limit_per_sec: num("RATE_LIMIT_PER_SEC", 0.5),
            link_allowlist: var(
                "LINK_ALLOWLIST",
                "acquisition.gov,worldbank.org,un.org,acme.example",
            )
            .split(',')
            .map(|s| s.trim().to_owned())
            .filter(|s| !s.is_empty())
            .collect(),
            stage_timeout: Duration::from_millis(num("STAGE_TIMEOUT_MS", 3_000)),
            cache_enabled: var("CACHE_ENABLED", "true") == "true",
            // 0.90 from the M10 sweep (evals/experiments/tau_semcache.json): 0 false hits on 7,284
            // same-key-term question pairs (hardest 0.874), 3/10 paraphrases hit; 0.95 hit none.
            semcache_threshold: num("SEMCACHE_THRESHOLD", 0.90),
            index_poll: Duration::from_secs(num("INDEX_POLL_SECS", 10)),
            prometheus_url: var("PROMETHEUS_URL", "http://127.0.0.1:9090"),
            grafana_url: std::env::var("GRAFANA_URL").ok().filter(|v| !v.is_empty()),
            repo_root: repo,
        }
    }
}
