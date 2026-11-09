//! Human-in-the-loop confirmation — the ONLY place a confirmation token is minted.
//!
//! `POST /v1/actions/{id}/confirm`: in one transaction, lock the proposal row and check it belongs to
//! this user, is still `proposed`, unexpired (5 min) and unused, and that its stored canonical
//! payload still hashes to `payload_hash`. Then mint a one-time HMAC token, store only its hash,
//! commit, and ask the brain to execute (no LLM involved). The MCP server verifies the token again,
//! marks it used atomically and executes with the proposal id as idempotency key.

use std::convert::Infallible;

use auth::{ConfirmationGrant, new_nonce, sha256_hex};
use axum::Json;
use axum::extract::{Path, State};
use axum::response::IntoResponse;
use axum::response::sse::{Event, Sse};
use metrics::counter;
use serde_json::{Value, json};
use telemetry::names;

use crate::brain::{self, pb};
use crate::http::{ApiError, AuthUser};
use crate::state::SharedState;

const TOKEN_TTL_SECS: i64 = 300;

#[derive(sqlx::FromRow)]
struct Proposal {
    user_id: String,
    action: String,
    payload_canonical: Option<String>,
    payload_hash: String,
    status: String,
    fresh: bool,
    unused: bool,
}

fn ev(name: &str, data: Value) -> Result<Event, Infallible> {
    Ok(Event::default().event(name).data(data.to_string()))
}

pub async fn confirm(
    State(s): State<SharedState>,
    user: AuthUser,
    Path(id): Path<String>,
) -> Result<impl IntoResponse, ApiError> {
    let me = user.identity().clone();
    let mut tx = s.db.begin().await?;
    let p = sqlx::query_as::<_, Proposal>(
        "SELECT user_id, action, payload_canonical, payload_hash, status, expires_at > now() AS fresh, \
         (used_at IS NULL AND token_hash IS NULL) AS unused FROM action_confirmations WHERE id = $1 FOR UPDATE",
    )
    .bind(&id)
    .fetch_optional(&mut *tx)
    .await?
    // Someone else's proposal looks exactly like a missing one (no existence oracle).
    .filter(|p| p.user_id == me.sub)
    .ok_or(ApiError::NotFound)?;
    if p.status != "proposed" || !p.unused {
        return Err(ApiError::Conflict(
            "This action was already confirmed or cancelled.".into(),
        ));
    }
    if !p.fresh {
        sqlx::query("UPDATE action_confirmations SET status = 'expired' WHERE id = $1")
            .bind(&id)
            .execute(&mut *tx)
            .await?;
        tx.commit().await?;
        return Err(ApiError::Conflict(
            "This proposal expired. Please ask again.".into(),
        ));
    }
    let canonical = p.payload_canonical.unwrap_or_default();
    if sha256_hex(canonical.as_bytes()) != p.payload_hash {
        // The stored payload changed after the user saw it: refuse loudly in logs, generically to the user.
        tracing::error!(proposal = %id, "payload hash mismatch on confirm");
        counter!(names::GUARD_BLOCKS, "layer" => "action_integrity").increment(1);
        return Err(ApiError::Conflict(
            "This proposal can't be confirmed. Please ask again.".into(),
        ));
    }
    let grant = ConfirmationGrant {
        proposal_id: id.clone(),
        user_id: me.sub.clone(),
        payload_hash: p.payload_hash.clone(),
        exp: time::OffsetDateTime::now_utc().unix_timestamp() + TOKEN_TTL_SECS,
        nonce: new_nonce(),
    };
    let token = s.confirm.mint(&grant);
    sqlx::query(
        "UPDATE action_confirmations SET token_hash = $1, status = 'confirmed' WHERE id = $2",
    )
    .bind(sha256_hex(token.as_bytes()))
    .bind(&id)
    .execute(&mut *tx)
    .await?;
    tx.commit().await?;
    sqlx::query(
        "INSERT INTO audit_log (actor_id, action, target, details) VALUES ($1, 'confirm', $2, $3)",
    )
    .bind(&me.sub)
    .bind(&id)
    .bind(json!({"action": p.action}))
    .execute(&s.db)
    .await?;

    let request_id = uuid::Uuid::new_v4().to_string();
    let obo = s
        .jwt
        .issue_obo(&me, &request_id)
        .map_err(|e| ApiError::Internal(e.into()))?;
    let req = pb::ConfirmRequest {
        request_id,
        claims: Some(brain::claims(&me)),
        proposal_id: id.clone(),
        confirmation_token: token,
        obo_token: obo,
    };
    let mut client = s.brain.clone();
    let action = p.action;
    let stream = async_stream::stream! {
        yield ev("meta", json!({"proposal_id": id}));
        let mut events = match client.execute_confirmed(brain::traced(req)).await {
            Ok(r) => r.into_inner(),
            Err(e) => {
                tracing::warn!(error = %e, "brain unavailable for confirmed action");
                yield ev("done", json!({"status": "unavailable", "message": "Actions are temporarily unavailable. Nothing was changed."}));
                return;
            }
        };
        let mut status = "failed";
        let mut result = Value::Null;
        while let Ok(Some(msg)) = events.message().await {
            match msg.event {
                Some(pb::agent_event::Event::ToolResult(t)) => {
                    result = serde_json::from_str(&t.result_json).unwrap_or(Value::Null);
                    counter!(names::ACTIONS, "tool" => action.clone(), "result" => if t.ok { "executed" } else { "failed" }).increment(1);
                }
                Some(pb::agent_event::Event::Final(f)) => {
                    status = if f.status == "done" { "executed" } else { "failed" };
                    yield ev("delta", json!({"text": f.text}));
                }
                Some(pb::agent_event::Event::Error(e)) => {
                    yield ev("delta", json!({"text": e.message}));
                }
                _ => {}
            }
        }
        yield ev("done", json!({"status": status, "result": result}));
    };
    Ok(Sse::new(stream))
}

pub async fn cancel(
    State(s): State<SharedState>,
    user: AuthUser,
    Path(id): Path<String>,
) -> Result<Json<Value>, ApiError> {
    let n = sqlx::query("UPDATE action_confirmations SET status = 'cancelled' WHERE id = $1 AND user_id = $2 AND status = 'proposed'")
        .bind(&id)
        .bind(&user.identity().sub)
        .execute(&s.db)
        .await?
        .rows_affected();
    if n == 0 {
        return Err(ApiError::NotFound);
    }
    Ok(Json(json!({"status": "cancelled"})))
}
