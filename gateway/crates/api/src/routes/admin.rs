//! Admin-only API backing the Insights pages. Everything here is diagnostic: thresholds,
//! dependency/breaker state, budgets and versions — never shown to non-admin users.

use axum::Json;
use axum::extract::{Path, State};
use serde_json::json;

use crate::http::{AdminUser, ApiError};
use crate::state::SharedState;

pub async fn runtime(State(s): State<SharedState>, admin: AdminUser) -> Json<serde_json::Value> {
    tracing::info!(admin = %admin.0.identity.sub, "admin runtime view");
    Json(json!({
        "thresholds": s.cfg.thresholds,
        "llm_providers": s.llm.provider_names(),
        "jev": {
            "configured": s.jev.configured(),
            "available": s.jev.available(),
            "breaker": format!("{:?}", s.jev.breaker.state()),
            "budget_spent_usd": s.jev.budget.spent_micros() as f64 / 1e6,
            "budget_usd": s.cfg.jev_budget_usd,
            "budget_status": format!("{:?}", s.jev.budget.status()),
            "router_questions_version": s.jev.questions.router_version,
            "gate_questions_version": s.jev.questions.gate_version,
        },
        "index_version": s.index_version(),
        "prompt_version": s.prompt.version,
        "embedder_loaded": s.embedder().is_some(),
    }))
}

/// Clear a user's injection strikes (unlocks a silently throttled/locked account). Audited.
pub async fn reset_strikes(
    State(s): State<SharedState>,
    admin: AdminUser,
    Path(user_id): Path<String>,
) -> Result<Json<serde_json::Value>, ApiError> {
    let removed = s
        .valkey
        .del(&format!("strikes:{user_id}"))
        .await
        .ok_or(ApiError::Unavailable("valkey"))?;
    sqlx::query("INSERT INTO audit_log (actor_id, action, target, details) VALUES ($1, 'reset_strikes', $2, '{}')")
        .bind(&admin.0.identity.sub)
        .bind(&user_id)
        .execute(&s.db)
        .await?;
    Ok(Json(json!({"user": user_id, "had_strikes": removed})))
}

/// Cache configuration and this replica's L1 sizes. Hit rates are in Prometheus
/// (`cache_requests_total{cache,layer,result}`).
pub async fn cache_stats(
    State(s): State<SharedState>,
    _admin: AdminUser,
) -> Json<serde_json::Value> {
    let l1: serde_json::Map<String, serde_json::Value> = s
        .caches
        .local_entries()
        .into_iter()
        .map(|(k, n)| (k.to_owned(), json!(n)))
        .collect();
    Json(json!({
        "enabled": s.cfg.cache_enabled,
        "semantic_threshold": s.cfg.semcache_threshold,
        "index_version": s.index_version(),
        "prompt_version": s.prompt.version,
        "l1_entries": l1,
    }))
}

/// Drop every cache entry (L1, Valkey L2 and semantic). For incidents, e.g. a bad prompt
/// or document that was answered from before it was fixed; audited.
pub async fn cache_flush(
    State(s): State<SharedState>,
    admin: AdminUser,
) -> Result<Json<serde_json::Value>, ApiError> {
    s.caches.clear_local();
    let mut l2_removed = 0_u64;
    for prefix in crate::caching::L2_PREFIXES {
        l2_removed += s.valkey.delete_matching(prefix).await.unwrap_or(0);
    }
    let semantic = match &s.caches.semantic {
        Some(sem) => sem.clear().await.is_ok(),
        None => false,
    };
    sqlx::query("INSERT INTO audit_log (actor_id, action, target, details) VALUES ($1, 'cache_flush', 'all', $2::jsonb)")
        .bind(&admin.0.identity.sub)
        .bind(json!({"l2_removed": l2_removed, "semantic_cleared": semantic}).to_string())
        .execute(&s.db)
        .await?;
    Ok(Json(
        json!({"l2_removed": l2_removed, "semantic_cleared": semantic}),
    ))
}
