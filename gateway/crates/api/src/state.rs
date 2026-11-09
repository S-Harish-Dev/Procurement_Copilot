//! Shared application state, built once at start-up.

use std::collections::VecDeque;
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::Duration;

use anyhow::Context;
use auth::{ConfirmationSigner, JwtKeys};
use base64::Engine as _;
use guard::InjectionScanner;
use jev::{JevClient, JevConfig, QuestionSets};
use metrics::gauge;
use metrics_exporter_prometheus::PrometheusHandle;
use policy::OpaClient;
use rand::RngExt;
use retrieval::{Bm25, QueryEmbedder, Retriever};
use sqlx::PgPool;
use sqlx::postgres::PgPoolOptions;
use telemetry::names;

use crate::caching::Caches;
use crate::config::Config;
use crate::valkey::Valkey;

pub type SharedState = Arc<AppState>;

pub struct SystemPrompt {
    pub version: &'static str,
    pub text: String,
    /// Random per process; if it ever appears in an answer the prompt is leaking.
    pub canary: String,
}

/// Recent end-to-end latencies of *answered* requests, used to pad blocked responses so they
/// are indistinguishable by timing.
pub struct LatencyWindow {
    samples: Mutex<VecDeque<f64>>,
}

impl LatencyWindow {
    const CAP: usize = 500;

    pub fn new() -> Self {
        Self {
            samples: Mutex::new(VecDeque::with_capacity(Self::CAP)),
        }
    }

    pub fn record(&self, secs: f64) {
        let mut s = self.samples.lock().expect("latency lock");
        if s.len() == Self::CAP {
            s.pop_front();
        }
        s.push_back(secs);
    }

    /// How long to keep holding a blocked response so its total latency is a random draw from the
    /// *empirical* distribution of real answers (minus time already spent). A uniform p50–p90 band
    /// skewed blocked responses slower than real ones — measurably (red-team timing check).
    pub fn sample_pad(&self, already: Duration) -> Duration {
        let s = self.samples.lock().expect("latency lock");
        let target = if s.len() < 20 {
            rand::rng().random_range(0.6..2.0)
        } else {
            s[rand::rng().random_range(0..s.len())]
        };
        Duration::from_secs_f64((target - already.as_secs_f64()).max(0.0))
    }
}

impl Default for LatencyWindow {
    fn default() -> Self {
        Self::new()
    }
}

pub struct AppState {
    pub cfg: Config,
    pub jwt: JwtKeys,
    pub confirm: ConfirmationSigner,
    pub brain: crate::brain::BrainClient,
    pub opa: OpaClient,
    pub llm: llm::Router,
    pub jev: JevClient,
    pub retriever: Retriever,
    pub embedder: OnceLock<QueryEmbedder>,
    pub classifier: OnceLock<classifier::InjectionClassifier>,
    /// Set once the classifier load attempt has finished, loaded or not: readiness waits for it so
    /// the first requests after a restart aren't served with guard layer 4 missing.
    pub classifier_settled: std::sync::atomic::AtomicBool,
    pub bm25: Bm25,
    pub scanner: InjectionScanner,
    pub db: PgPool,
    pub valkey: Valkey,
    pub index_version: AtomicI64,
    pub prompt: SystemPrompt,
    pub latencies: LatencyWindow,
    pub metrics: PrometheusHandle,
    pub caches: Caches,
    pub traces: crate::traces::TraceRing,
}

impl AppState {
    pub async fn build(cfg: Config, metrics: PrometheusHandle) -> anyhow::Result<SharedState> {
        let read = |p: &std::path::Path| {
            std::fs::read_to_string(p).with_context(|| format!("reading {}", p.display()))
        };

        let jwt = JwtKeys::from_pem(
            &std::fs::read(&cfg.jwt_private_key)
                .with_context(|| format!("JWT key {}", cfg.jwt_private_key.display()))?,
            &std::fs::read(&cfg.jwt_public_key)
                .with_context(|| format!("JWT key {}", cfg.jwt_public_key.display()))?,
        )?;
        let confirm_key = base64::engine::general_purpose::STANDARD
            .decode(&cfg.confirmation_key_b64)
            .context("CONFIRMATION_HMAC_KEY must be base64 (generate with `make env`)")?;
        let confirm = ConfirmationSigner::new(&confirm_key)?;
        anyhow::ensure!(
            cfg.internal_token.len() >= 32,
            "INTERNAL_API_TOKEN must be at least 32 chars (make env)"
        );

        let providers = llm::LlmConfig::from_toml(&read(&cfg.providers_toml)?)
            .context("parsing providers.toml")?;
        let llm = llm::Router::new(providers, |k| std::env::var(k).ok());
        if llm.provider_names().is_empty() {
            tracing::warn!(
                "no LLM provider has an API key: chat will answer 'temporarily unavailable'"
            );
        }

        let questions = QuestionSets::from_json(
            &read(&cfg.prompts_dir.join("router_questions_v1.json"))?,
            &read(&cfg.prompts_dir.join("gate_questions_v1.json"))?,
        )?;
        let jev_cfg = JevConfig {
            base_url: cfg.jev_base_url.clone(),
            api_key: cfg.jev_api_key.clone(),
            model: cfg.jev_model.clone(),
            timeout: cfg.jev_timeout,
            price_in_per_m: cfg.jev_price_in_per_m,
            budget_usd: cfg.jev_budget_usd,
        };
        let valkey = Valkey::new();
        valkey.spawn_connector(cfg.valkey_url.clone());
        // Budget meter survives restarts: seed from the persisted spend if Valkey is reachable.
        tokio::time::sleep(Duration::from_millis(300)).await;
        let spent = valkey.get_i64("jev:spent_micros").await.unwrap_or(0).max(0) as u64;
        let jev = JevClient::new(jev_cfg, questions, spent);
        if !jev.configured() {
            tracing::warn!("TYPESAFE_API_KEY not set: router and gate run in degraded mode");
        }

        let canary = format!("CANARY-{}", uuid::Uuid::new_v4().simple());
        let prompt_text = read(&cfg.prompts_dir.join("system_v1.md"))?.replace("{canary}", &canary);

        let db = PgPoolOptions::new()
            .max_connections(10)
            .acquire_timeout(Duration::from_secs(2))
            .connect_lazy(&cfg.database_url)
            .context("DATABASE_URL")?;

        let semantic = cache::SemanticCache::connect(
            &cfg.qdrant_grpc_url,
            cfg.semcache_threshold,
            Duration::from_secs(24 * 3600),
            retrieval::embed::DIM,
        )?;
        let caches = Caches::new(&valkey, Some(semantic));

        let state = Arc::new(AppState {
            caches,
            traces: crate::traces::TraceRing::default(),
            opa: OpaClient::new(&cfg.opa_url, Duration::from_millis(300)),
            retriever: Retriever::connect(&cfg.qdrant_grpc_url)?,
            brain: crate::brain::connect_lazy(&cfg.brain_url)?,
            embedder: OnceLock::new(),
            classifier: OnceLock::new(),
            classifier_settled: std::sync::atomic::AtomicBool::new(false),
            bm25: Bm25::new(&read(&cfg.assets_dir.join("bm25_stopwords_english.txt"))?),
            scanner: InjectionScanner::from_json(&read(
                &cfg.prompts_dir.join("injection_patterns_v1.json"),
            )?)?,
            prompt: SystemPrompt {
                version: "v1",
                text: prompt_text,
                canary,
            },
            latencies: LatencyWindow::new(),
            index_version: AtomicI64::new(0),
            jwt,
            confirm,
            llm,
            jev,
            db,
            valkey,
            metrics,
            cfg,
        });
        spawn_background(state.clone());
        Ok(state)
    }

    pub fn index_version(&self) -> i64 {
        self.index_version.load(Ordering::Relaxed)
    }

    pub fn embedder(&self) -> Option<&QueryEmbedder> {
        self.embedder.get()
    }

    pub fn classifier(&self) -> Option<&classifier::InjectionClassifier> {
        self.classifier.get()
    }
}

/// Background jobs: load the embedding model, keep the active index version fresh, export the
/// Jev-degraded gauge.
fn spawn_background(state: SharedState) {
    let s = state.clone();
    tokio::spawn(async move {
        // ONE ONNX Runtime environment per process, shared by the embedder and the classifier.
        let env = match ort::init().with_name("gateway").build() {
            Ok(e) => e,
            Err(e) => {
                tracing::error!(error = %e, "ONNX Runtime unavailable (ORT_DYLIB_PATH?): Q&A and classifier disabled");
                return;
            }
        };
        let (n, threads, cache, env2) = (
            s.cfg.embedder_instances,
            s.cfg.embedder_threads,
            s.cfg.fastembed_cache.clone(),
            env.clone(),
        );
        match tokio::task::spawn_blocking(move || QueryEmbedder::load(n, threads, cache, &env2))
            .await
        {
            Ok(Ok(e)) => {
                let _ = s.embedder.set(e);
                tracing::info!("query embedder loaded");
            }
            Ok(Err(e)) => {
                tracing::error!(error = %e, "query embedder failed to load; Q&A unavailable")
            }
            Err(e) => tracing::error!(error = %e, "embedder loader panicked"),
        }
        let (dir, n, threads) = (
            s.cfg.classifier_dir.clone(),
            s.cfg.classifier_instances,
            s.cfg.classifier_threads,
        );
        match tokio::task::spawn_blocking(move || {
            classifier::InjectionClassifier::load(&dir, &env, n, threads)
        })
        .await
        {
            Ok(Ok(c)) => {
                tracing::info!(model = %c.model_name, "injection classifier loaded");
                let _ = s.classifier.set(c);
            }
            Ok(Err(e)) => {
                tracing::warn!(error = %e, "injection classifier unavailable: guard layer 4 off")
            }
            Err(e) => tracing::error!(error = %e, "classifier loader panicked"),
        }
        s.classifier_settled.store(true, Ordering::Release);
    });
    tokio::spawn(async move {
        let mut semcache_ready = false;
        loop {
            if !semcache_ready && let Some(sem) = &state.caches.semantic {
                match sem.ensure_collection().await {
                    Ok(()) => semcache_ready = true,
                    Err(e) => tracing::warn!(error = %e, "semantic cache collection not ready"),
                }
            }
            match sqlx::query_scalar::<_, i32>("SELECT version FROM index_versions WHERE is_active")
                .fetch_optional(&state.db)
                .await
            {
                Ok(Some(v)) => {
                    let v = i64::from(v);
                    let old = state.index_version.swap(v, Ordering::Relaxed);
                    if old != v {
                        tracing::info!(index_version = v, "active index version");
                    }
                    // Keys already carry the version; dropping L1 and the stale semantic points
                    // just frees the memory of entries that can never match again.
                    if old != 0 && old != v {
                        state.caches.clear_local();
                        if let Some(sem) = &state.caches.semantic
                            && let Err(e) = sem.purge_other_versions(v).await
                        {
                            tracing::warn!(error = %e, "could not purge stale semantic cache");
                        }
                        tracing::info!(from = old, to = v, "index bump: caches invalidated");
                    }
                    gauge!(names::DEPENDENCY_UP, "dependency" => "postgres").set(1.0);
                }
                Ok(None) => tracing::warn!("no active index version: run `make ingest`"),
                Err(e) => {
                    tracing::warn!(error = %e, "postgres unreachable");
                    gauge!(names::DEPENDENCY_UP, "dependency" => "postgres").set(0.0);
                }
            }
            gauge!(names::JEV_DEGRADED).set(if state.jev.available() { 0.0 } else { 1.0 });
            // Persist Jev spend so a restart doesn't reset the budget meter (90-day TTL).
            let spent = state.jev.budget.spent_micros();
            if spent > 0 {
                state
                    .valkey
                    .set_ex("jev:spent_micros", &spent.to_string(), 90 * 86_400)
                    .await;
            }
            tokio::time::sleep(state.cfg.index_poll).await;
        }
    });
}
