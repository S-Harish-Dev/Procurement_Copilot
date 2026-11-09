//! Admin-only evaluation endpoints (disabled unless `EVAL_ENDPOINTS=true`).
//!
//! `POST /admin/eval/retrieve` runs the *production* retrieval path — the gateway's own query
//! embedder, BM25 port, OPA scope and Qdrant hybrid query — **as a given persona**, and returns the
//! pre-gate candidates (optionally with gate decisions). Evals therefore measure the code that serves
//! users, not a re-implementation. Knobs (dense/BM25 on/off, k) drive the retrieval ablations (M10)
//! and the "break retrieval -> the gate must fail" check (M4).
//!
//! Impersonation is powerful, so: admin token required, off by default, every call logged.

use auth::{Identity, Role};
use axum::Json;
use axum::extract::State;
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::http::{AdminUser, ApiError};
use crate::state::SharedState;

#[derive(Deserialize)]
pub struct RetrieveRequest {
    pub query: String,
    pub as_user: String,
    #[serde(default = "yes")]
    pub use_dense: bool,
    #[serde(default = "yes")]
    pub use_sparse: bool,
    #[serde(default = "k40")]
    pub dense_k: u64,
    #[serde(default = "k40")]
    pub sparse_k: u64,
    #[serde(default = "k20")]
    pub top_k: u64,
    #[serde(default)]
    pub with_gate: bool,
}

fn yes() -> bool {
    true
}
fn k40() -> u64 {
    40
}
fn k20() -> u64 {
    20
}

#[derive(Serialize)]
struct Candidate {
    rank: usize,
    chunk_id: String,
    doc_id: String,
    classification: String,
    department: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    relevance: Option<f64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    injection: Option<f64>,
}

#[derive(sqlx::FromRow)]
struct UserRow {
    name: String,
    role: String,
    department: String,
    spend_limit: f64,
    approval_limit: f64,
}

pub async fn retrieve(
    State(s): State<SharedState>,
    admin: AdminUser,
    Json(req): Json<RetrieveRequest>,
) -> Result<Json<serde_json::Value>, ApiError> {
    if std::env::var("EVAL_ENDPOINTS").as_deref() != Ok("true") {
        return Err(ApiError::NotFound);
    }
    let u = sqlx::query_as::<_, UserRow>(
        "SELECT name, role::text AS role, department_id AS department, spend_limit::float8 AS spend_limit, \
         approval_limit::float8 AS approval_limit FROM users WHERE id = $1",
    )
    .bind(&req.as_user)
    .fetch_optional(&s.db)
    .await?
    .ok_or(ApiError::NotFound)?;
    let role = match u.role.as_str() {
        "employee" => Role::Employee,
        "manager" => Role::Manager,
        "finance" => Role::Finance,
        _ => Role::Admin,
    };
    let identity = Identity {
        sub: req.as_user.clone(),
        name: u.name,
        role,
        dept: u.department,
        spend_limit: u.spend_limit,
        approval_limit: u.approval_limit,
    };
    tracing::info!(admin = %admin.0.identity.sub, as_user = %req.as_user, "eval retrieve");

    let normalized = guard::normalize(&req.query);
    let mut pii = guard::PiiMap::default();
    let masked = guard::mask_pii(&normalized, &mut pii);
    let scope = s.opa.retrieval_scope(&identity).await;
    let embedder = s.embedder().ok_or(ApiError::Unavailable("embedder"))?;
    let t = std::time::Instant::now();
    let dense = if req.use_dense {
        embedder
            .embed(&masked)
            .await
            .map_err(|_| ApiError::Unavailable("embed"))?
    } else {
        Vec::new()
    };
    let sparse = if req.use_sparse {
        s.bm25.query_indices(&masked)
    } else {
        Vec::new()
    };
    let retriever = s.retriever.clone().with_params(retrieval::HybridParams {
        dense_k: req.dense_k,
        sparse_k: req.sparse_k,
        top_k: req.top_k,
    });
    let hits = retriever
        .hybrid(dense, sparse, &scope, s.index_version())
        .await
        .map_err(|_| ApiError::Unavailable("qdrant"))?;
    let retrieve_ms = t.elapsed().as_millis() as u64;
    let gate = if req.with_gate && !hits.is_empty() {
        let passages: Vec<String> = hits.iter().map(|h| h.text.clone()).collect();
        s.jev.gate(&masked, &passages).await.ok()
    } else {
        None
    };
    let candidates: Vec<Candidate> = hits
        .iter()
        .enumerate()
        .map(|(i, h)| Candidate {
            rank: i + 1,
            chunk_id: h.chunk_id.clone(),
            doc_id: h.doc_id.clone(),
            classification: h.classification.clone(),
            department: h.department.clone(),
            relevance: gate.as_ref().map(|g| g.relevance[i]),
            injection: gate.as_ref().map(|g| g.injection[i]),
        })
        .collect();
    Ok(Json(json!({
        "candidates": candidates,
        "scope": {"grants": scope.grants, "degraded": scope.degraded},
        "index_version": s.index_version(),
        "retrieve_ms": retrieve_ms,
        "gate": gate.is_some(),
    })))
}

#[derive(Deserialize)]
pub struct GuardRequest {
    pub text: String,
}

/// `POST /admin/eval/guard` — local guard layers only (normalize -> regex -> classifier), no Jev, no
/// LLM. Lets `make eval-fast` measure detection and false-positive rates deterministically in seconds.
pub async fn guard(
    State(s): State<SharedState>,
    _admin: AdminUser,
    Json(req): Json<GuardRequest>,
) -> Result<Json<serde_json::Value>, ApiError> {
    if std::env::var("EVAL_ENDPOINTS").as_deref() != Ok("true") {
        return Err(ApiError::NotFound);
    }
    let normalized = guard::normalize(&req.text);
    let regex = s.scanner.scan(&normalized);
    let p_cls = match s.classifier() {
        Some(c) => Some(
            c.score(&normalized)
                .await
                .map_err(|_| ApiError::Unavailable("classifier"))?,
        ),
        None => None,
    };
    // Same rule as the request path, with Jev treated as unavailable (local layers only).
    let verdict = crate::routes::chat::injection_verdict(
        &regex,
        None,
        p_cls.map(f64::from),
        normalized.split_whitespace().count(),
        &s.cfg.thresholds,
    );
    Ok(Json(json!({
        "regex_hits": regex.hits,
        "regex_high": regex.high,
        "p_classifier": p_cls,
        "blocked": verdict.is_some(),
        "layer": verdict,
    })))
}
