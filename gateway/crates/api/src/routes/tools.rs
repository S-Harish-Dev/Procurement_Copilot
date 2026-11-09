//! `POST /v1/tools/{name}` — direct tool calls for the UI (request lists, approval inbox, budgets,
//! approve/reject buttons). Goes through the brain's `CallTool` to the same MCP servers with an OBO
//! token, so the same OPA checks apply; write tools only ever return proposals here.

use axum::Json;
use axum::extract::{Path, State};
use serde::Deserialize;
use serde_json::{Value, json};

use crate::brain::{self, pb};
use crate::http::{ApiError, AuthUser};
use crate::state::SharedState;

#[derive(Deserialize, Default)]
pub struct ToolCall {
    #[serde(default)]
    pub args: Value,
}

pub async fn call(
    State(s): State<SharedState>,
    user: AuthUser,
    Path(name): Path<String>,
    body: Option<Json<ToolCall>>,
) -> Result<Json<Value>, ApiError> {
    let args = body
        .map(|b| b.0.args)
        .filter(|a| !a.is_null())
        .unwrap_or_else(|| json!({}));
    call_tool(&s, user.identity(), &name, args).await.map(Json)
}

pub async fn call_tool(
    s: &SharedState,
    user: &auth::Identity,
    name: &str,
    args: Value,
) -> Result<Value, ApiError> {
    let request_id = uuid::Uuid::new_v4().to_string();
    let obo = s
        .jwt
        .issue_obo(user, &request_id)
        .map_err(|e| ApiError::Internal(e.into()))?;
    let req = pb::ToolRequest {
        request_id,
        obo_token: obo,
        name: name.to_owned(),
        args_json: args.to_string(),
    };
    let mut client = s.brain.clone();
    let resp = client.call_tool(brain::traced(req)).await.map_err(|e| {
        tracing::warn!(error = %e, tool = name, "brain unavailable for tool call");
        ApiError::Unavailable("brain")
    })?;
    Ok(serde_json::from_str(&resp.into_inner().result_json).unwrap_or(Value::Null))
}
