//! Errors and request extractors shared by the routes.

use auth::{Claims, Identity, Role, TokenType};
use axum::Json;
use axum::extract::FromRequestParts;
use axum::http::StatusCode;
use axum::http::request::Parts;
use axum::response::{IntoResponse, Response};
use serde_json::json;

use crate::state::SharedState;

#[derive(Debug, thiserror::Error)]
pub enum ApiError {
    #[error("unauthorized")]
    Unauthorized,
    #[error("forbidden: {0}")]
    Forbidden(String),
    #[error("not found")]
    NotFound,
    #[error("bad request: {0}")]
    BadRequest(String),
    #[error("conflict: {0}")]
    Conflict(String),
    #[error("too many requests")]
    RateLimited,
    #[error("temporarily unavailable: {0}")]
    Unavailable(&'static str),
    #[error("internal error")]
    Internal(#[from] anyhow::Error),
}

impl IntoResponse for ApiError {
    fn into_response(self) -> Response {
        let (status, message) = match &self {
            ApiError::Unauthorized => {
                (StatusCode::UNAUTHORIZED, "Please sign in again.".to_owned())
            }
            ApiError::Forbidden(m) => (StatusCode::FORBIDDEN, m.clone()),
            ApiError::NotFound => (StatusCode::NOT_FOUND, "Not found.".to_owned()),
            ApiError::BadRequest(m) => (StatusCode::BAD_REQUEST, m.clone()),
            ApiError::Conflict(m) => (StatusCode::CONFLICT, m.clone()),
            ApiError::RateLimited => (
                StatusCode::TOO_MANY_REQUESTS,
                "You're sending requests too quickly. Please wait a moment.".to_owned(),
            ),
            ApiError::Unavailable(_) => (
                StatusCode::SERVICE_UNAVAILABLE,
                "This service is temporarily unavailable. Please try again shortly.".to_owned(),
            ),
            ApiError::Internal(e) => {
                // Details go to logs/traces only; users get a generic message (no information leakage).
                tracing::error!(error = %e, "internal error");
                (
                    StatusCode::INTERNAL_SERVER_ERROR,
                    "Something went wrong. Please try again.".to_owned(),
                )
            }
        };
        (status, Json(json!({"error": message}))).into_response()
    }
}

impl From<sqlx::Error> for ApiError {
    fn from(e: sqlx::Error) -> Self {
        match e {
            sqlx::Error::PoolTimedOut | sqlx::Error::Io(_) | sqlx::Error::PoolClosed => {
                ApiError::Unavailable("postgres")
            }
            sqlx::Error::RowNotFound => ApiError::NotFound,
            other => ApiError::Internal(other.into()),
        }
    }
}

/// A verified user (RS256 user token from `Authorization: Bearer ...`).
pub struct AuthUser(pub Claims);

impl AuthUser {
    pub fn identity(&self) -> &Identity {
        &self.0.identity
    }
    pub fn is_admin(&self) -> bool {
        self.0.identity.role == Role::Admin
    }
}

impl FromRequestParts<SharedState> for AuthUser {
    type Rejection = ApiError;

    async fn from_request_parts(
        parts: &mut Parts,
        state: &SharedState,
    ) -> Result<Self, Self::Rejection> {
        let header = parts
            .headers
            .get(axum::http::header::AUTHORIZATION)
            .and_then(|v| v.to_str().ok());
        let token = header
            .and_then(|h| h.strip_prefix("Bearer "))
            .ok_or(ApiError::Unauthorized)?;
        let claims = state
            .jwt
            .verify(token, TokenType::User)
            .map_err(|_| ApiError::Unauthorized)?;
        Ok(AuthUser(claims))
    }
}

/// Admin-only routes (Insights, traces, reason codes).
pub struct AdminUser(pub Claims);

impl FromRequestParts<SharedState> for AdminUser {
    type Rejection = ApiError;

    async fn from_request_parts(
        parts: &mut Parts,
        state: &SharedState,
    ) -> Result<Self, Self::Rejection> {
        let user = AuthUser::from_request_parts(parts, state).await?;
        if !user.is_admin() {
            return Err(ApiError::Forbidden("Administrators only.".into()));
        }
        Ok(AdminUser(user.0))
    }
}

/// Current trace id (hex) for admin-only trace links.
pub fn current_trace_id() -> Option<String> {
    use opentelemetry::trace::TraceContextExt;
    use tracing_opentelemetry::OpenTelemetrySpanExt;
    let ctx = tracing::Span::current().context();
    let span_ctx = ctx.span().span_context().clone();
    span_ctx.is_valid().then(|| span_ctx.trace_id().to_string())
}
