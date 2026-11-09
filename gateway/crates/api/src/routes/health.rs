//! Liveness (`/healthz`: the process is up) vs readiness (`/readyz`: dependencies).
//! Readiness reports each dependency; the gateway is "ready" for Q&A when the embedder is loaded
//! and Qdrant answers and the injection classifier's load attempt has finished (loaded, or failed
//! and alerted as degraded) — other outages degrade specific features rather than the whole service.

use axum::Json;
use axum::extract::State;
use axum::http::StatusCode;
use serde_json::json;

use crate::state::SharedState;

pub async fn healthz() -> &'static str {
    "ok"
}

pub async fn readyz(State(s): State<SharedState>) -> (StatusCode, Json<serde_json::Value>) {
    let (opa, qdrant, valkey, postgres) = tokio::join!(
        s.opa.health(),
        s.retriever.health(),
        s.valkey.is_up(),
        async { sqlx::query("SELECT 1").execute(&s.db).await.is_ok() },
    );
    let embedder = s.embedder().is_some();
    let classifier_settled = s
        .classifier_settled
        .load(std::sync::atomic::Ordering::Acquire);
    let ready = embedder && qdrant && classifier_settled;
    let body = json!({
        "ready": ready,
        "dependencies": {
            "embedder": embedder, "classifier": s.classifier().is_some(), "qdrant": qdrant, "opa": opa, "valkey": valkey, "postgres": postgres,
            "jev": s.jev.available(), "llm_providers": s.llm.provider_names(),
        },
        "index_version": s.index_version(),
    });
    (
        if ready {
            StatusCode::OK
        } else {
            StatusCode::SERVICE_UNAVAILABLE
        },
        Json(body),
    )
}
