//! `POST /v1/feedback`: 👍/👎 on an answer. Stored PII-masked; reviewed rows become eval
//! candidates. One vote per user per answer (voting again replaces it).

use axum::Json;
use axum::extract::State;
use guard::{PiiMap, mask_pii, normalize};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::http::{ApiError, AuthUser};
use crate::state::SharedState;

const MAX_CHARS: usize = 4_000;

#[derive(Deserialize)]
pub struct Feedback {
    pub request_id: String,
    pub rating: String,
    #[serde(default)]
    pub question: String,
    #[serde(default)]
    pub answer: String,
    #[serde(default)]
    pub comment: String,
}

fn masked(text: &str) -> String {
    let short: String = text.chars().take(MAX_CHARS).collect();
    mask_pii(&normalize(&short), &mut PiiMap::default())
}

pub async fn submit(
    State(s): State<SharedState>,
    user: AuthUser,
    Json(f): Json<Feedback>,
) -> Result<Json<Value>, ApiError> {
    let rating: i16 = match f.rating.as_str() {
        "up" => 1,
        "down" => -1,
        _ => {
            return Err(ApiError::BadRequest(
                "rating must be \"up\" or \"down\".".into(),
            ));
        }
    };
    if f.request_id.is_empty() || f.request_id.len() > 64 {
        return Err(ApiError::BadRequest("Missing request id.".into()));
    }
    let id = user.identity();
    sqlx::query(
        "INSERT INTO feedback (request_id, user_id, role, rating, question, answer, comment)
         VALUES ($1, $2, $3::user_role, $4, $5, $6, $7)
         ON CONFLICT (request_id, user_id) DO UPDATE
           SET rating = EXCLUDED.rating, comment = EXCLUDED.comment, created_at = now()",
    )
    .bind(&f.request_id)
    .bind(&id.sub)
    .bind(id.role.as_str())
    .bind(rating)
    .bind(masked(&f.question))
    .bind(masked(&f.answer))
    .bind(masked(&f.comment))
    .execute(&s.db)
    .await?;
    Ok(Json(json!({"ok": true})))
}
