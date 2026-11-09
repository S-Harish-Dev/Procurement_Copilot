//! `GET /v1/files/po/{pr_id}` — download a generated purchase-order PDF.
//!
//! Authorization reuses the MCP read tool: if `get_purchase_request` refuses this user, so does the
//! download (one policy path). The id is validated against a strict pattern before it touches the
//! filesystem, so it can't be used for path traversal.

use axum::body::Body;
use axum::extract::{Path, State};
use axum::http::header;
use axum::response::{IntoResponse, Response};
use serde_json::json;

use crate::http::{ApiError, AuthUser};
use crate::routes::tools::call_tool;
use crate::state::SharedState;

fn valid_pr_id(id: &str) -> bool {
    let b = id.as_bytes();
    id.len() == 13
        && id.starts_with("PR-")
        && b[7] == b'-'
        && b[3..7].iter().chain(&b[8..]).all(u8::is_ascii_digit)
}

pub async fn po(
    State(s): State<SharedState>,
    user: AuthUser,
    Path(pr_id): Path<String>,
) -> Result<Response, ApiError> {
    if !valid_pr_id(&pr_id) {
        return Err(ApiError::NotFound);
    }
    let pr = call_tool(
        &s,
        user.identity(),
        "get_purchase_request",
        json!({"pr_id": pr_id}),
    )
    .await?;
    if pr.get("id").and_then(|v| v.as_str()) != Some(pr_id.as_str()) {
        return Err(ApiError::NotFound); // denied and missing look the same
    }
    let path = s.cfg.po_dir.join(format!("{pr_id}.pdf"));
    let bytes = tokio::fs::read(&path)
        .await
        .map_err(|_| ApiError::NotFound)?;
    Ok((
        [
            (header::CONTENT_TYPE, "application/pdf".to_owned()),
            (
                header::CONTENT_DISPOSITION,
                format!("attachment; filename=\"PO-{}.pdf\"", &pr_id[3..]),
            ),
        ],
        Body::from(bytes),
    )
        .into_response())
}

#[cfg(test)]
mod tests {
    use super::valid_pr_id;

    #[test]
    fn pr_id_validation_blocks_traversal() {
        assert!(valid_pr_id("PR-2026-00042"));
        for bad in [
            "../etc/passwd",
            "PR-2026-0042",
            "PR-2026-00042/../x",
            "PR-20a6-00042",
            "pr-2026-00042",
        ] {
            assert!(!valid_pr_id(bad), "{bad}");
        }
    }
}
