//! Demo login (one-click persona cards) and the current-user endpoint.
//!
//! There are no passwords: this is a local demo with seeded users, gated by `DEMO_LOGIN`. Every
//! token is a real RS256 JWT verified on each request, so the authorization path downstream is
//! exactly what a real IdP integration would exercise.

use auth::{Identity, Role};
use axum::Json;
use axum::extract::State;
use serde::{Deserialize, Serialize};
use serde_json::json;

use crate::http::{ApiError, AuthUser};
use crate::state::SharedState;

#[derive(Serialize, sqlx::FromRow)]
pub struct Persona {
    pub id: String,
    pub name: String,
    pub email: String,
    pub role: String,
    pub department: String,
    pub spend_limit: f64,
    pub approval_limit: f64,
}

// A macro, not a const + format!: sqlx 0.9 rejects runtime-built SQL strings (injection guard),
// and concat! keeps every query a compile-time literal.
macro_rules! personas_sql {
    ($tail:literal) => {
        concat!(
            "SELECT id, name, email, role::text AS role, department_id AS department, ",
            "spend_limit::float8 AS spend_limit, approval_limit::float8 AS approval_limit FROM users ",
            $tail
        )
    };
}

fn role(s: &str) -> Option<Role> {
    Some(match s {
        "employee" => Role::Employee,
        "manager" => Role::Manager,
        "finance" => Role::Finance,
        "admin" => Role::Admin,
        _ => return None,
    })
}

pub async fn personas(State(s): State<SharedState>) -> Result<Json<Vec<Persona>>, ApiError> {
    if !s.cfg.demo_login {
        return Err(ApiError::NotFound);
    }
    let rows = sqlx::query_as::<_, Persona>(personas_sql!("ORDER BY role, department_id, name"))
        .fetch_all(&s.db)
        .await?;
    Ok(Json(rows))
}

#[derive(Deserialize)]
pub struct LoginRequest {
    pub user_id: String,
}

pub async fn login(
    State(s): State<SharedState>,
    Json(req): Json<LoginRequest>,
) -> Result<Json<serde_json::Value>, ApiError> {
    if !s.cfg.demo_login {
        return Err(ApiError::NotFound);
    }
    let p = sqlx::query_as::<_, Persona>(personas_sql!("WHERE id = $1"))
        .bind(&req.user_id)
        .fetch_optional(&s.db)
        .await?
        .ok_or(ApiError::Unauthorized)?;
    let identity = Identity {
        sub: p.id.clone(),
        name: p.name.clone(),
        role: role(&p.role)
            .ok_or_else(|| ApiError::Internal(anyhow::anyhow!("bad role {}", p.role)))?,
        dept: p.department.clone(),
        spend_limit: p.spend_limit,
        approval_limit: p.approval_limit,
    };
    let token = s
        .jwt
        .issue_user(&identity, s.cfg.user_token_ttl_secs)
        .map_err(|e| ApiError::Internal(e.into()))?;
    tracing::info!(user = %p.id, role = %p.role, "login");
    Ok(Json(
        json!({"token": token, "expires_in": s.cfg.user_token_ttl_secs, "user": p}),
    ))
}

pub async fn me(State(s): State<SharedState>, user: AuthUser) -> Json<serde_json::Value> {
    let can_view_pii = s
        .opa
        .decide(user.identity(), "view_pii", json!({}))
        .await
        .is_ok_and(|d| d.allow);
    Json(json!({"user": user.identity(), "can_view_pii": can_view_pii}))
}
