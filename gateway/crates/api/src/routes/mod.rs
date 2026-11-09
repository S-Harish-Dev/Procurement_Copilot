pub mod actions;
pub mod admin;
pub mod agent;
pub mod auth;
pub mod chat;
pub mod eval;
pub mod feedback;
pub mod files;
pub mod health;
pub mod insights;
pub mod internal;
pub mod tools;

use axum::Router;
use axum::routing::{get, post};
use tower_http::cors::CorsLayer;
use tower_http::trace::TraceLayer;

use crate::state::SharedState;

/// Public API (port 8080).
pub fn public(state: SharedState) -> Router {
    Router::new()
        .route("/healthz", get(health::healthz))
        .route("/readyz", get(health::readyz))
        .route("/v1/auth/personas", get(auth::personas))
        .route("/v1/auth/login", post(auth::login))
        .route("/v1/me", get(auth::me))
        .route("/v1/chat", post(chat::chat))
        .route("/v1/actions/{id}/confirm", post(actions::confirm))
        .route("/v1/actions/{id}/cancel", post(actions::cancel))
        .route("/v1/tools/{name}", post(tools::call))
        .route("/v1/files/po/{pr_id}", get(files::po))
        .route("/admin/runtime", get(admin::runtime))
        .route(
            "/admin/users/{id}/reset-strikes",
            post(admin::reset_strikes),
        )
        .route("/v1/feedback", post(feedback::submit))
        .route("/admin/metrics/overview", get(insights::overview))
        .route("/admin/evals", get(insights::evals))
        .route("/admin/evals/{run_id}", get(insights::eval_run))
        .route("/admin/experiments", get(insights::experiments))
        .route("/admin/traces", get(insights::traces))
        .route("/admin/traces/{id}", get(insights::trace))
        .route("/admin/cache", get(admin::cache_stats))
        .route("/admin/cache/flush", post(admin::cache_flush))
        .route("/admin/eval/retrieve", post(eval::retrieve))
        .route("/admin/eval/guard", post(eval::guard))
        // The web UI runs on its own origin in dev (Next.js :3001); it authenticates with a bearer
        // token, not cookies, so a permissive CORS policy doesn't enable CSRF.
        .layer(CorsLayer::permissive())
        .layer(TraceLayer::new_for_http())
        .with_state(state)
}

/// Internal LLM proxy for the brain (port 8081, never published).
pub fn internal(state: SharedState) -> Router {
    Router::new()
        .route("/v1/chat/completions", post(internal::chat_completions))
        .route("/healthz", get(health::healthz))
        .with_state(state)
}

/// Prometheus scrape endpoint (port 9464).
pub fn metrics(state: SharedState) -> Router {
    Router::new()
        .route(
            "/metrics",
            get(
                |axum::extract::State(s): axum::extract::State<SharedState>| async move {
                    s.metrics.render()
                },
            ),
        )
        .with_state(state)
}
