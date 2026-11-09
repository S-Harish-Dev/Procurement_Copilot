//! Action path: run the agent in the brain over gRPC and translate its events to SSE.
//!
//! The gateway mints a 60 s on-behalf-of token for this request only; the brain forwards it to the
//! MCP servers, which verify it and re-check policy. Agent text passes through the same output guard
//! as RAG answers (PII masking, link/image stripping, canary). Proposals become `proposal` events
//! the UI renders as ActionCards — nothing is executed until `/v1/actions/{id}/confirm`.

use auth::Identity;
use futures::stream::BoxStream;
use guard::{Block, OutputPolicy, OutputReport, PiiMap, Segmenter, scan_segment};
use jev::Intent;
use llm::Tier;
use metrics::counter;
use serde_json::{Value, json};
use telemetry::names;

use crate::brain::{self, pb};
use crate::routes::chat::{NO_ANSWER_TEMPLATE, UNAVAILABLE_TEMPLATE};
use crate::state::SharedState;

/// (SSE event name, data, final status if this item decides it)
pub type AgentItem = (&'static str, Value, Option<&'static str>);

#[allow(clippy::too_many_arguments)]
pub fn run(
    s: &SharedState,
    user: &Identity,
    tier: Tier,
    intent: Intent,
    masked_query: String,
    history: Vec<(String, String)>,
    may_see_pii: bool,
    pii: PiiMap,
) -> BoxStream<'static, AgentItem> {
    let s = s.clone();
    let user = user.clone();
    Box::pin(async_stream::stream! {
        let request_id = uuid::Uuid::new_v4().to_string();
        let Ok(obo) = s.jwt.issue_obo(&user, &request_id) else {
            yield ("delta", json!({"text": UNAVAILABLE_TEMPLATE}), Some("unavailable"));
            return;
        };
        let req = pb::AgentRequest {
            request_id,
            claims: Some(brain::claims(&user)),
            scope_hash: String::new(),
            masked_query,
            history: history.into_iter().map(|(role, content)| pb::Turn { role, content }).collect(),
            model_tier: tier.as_str().to_owned(),
            obo_token: obo,
            intent: intent.as_str().to_owned(),
        };
        let mut client = s.brain.clone();
        let mut events = match client.run_agent(brain::traced(req)).await {
            Ok(r) => r.into_inner(),
            Err(e) => {
                tracing::warn!(error = %e, "brain unavailable");
                counter!(names::ACTIONS, "tool" => "agent", "result" => "brain_unavailable").increment(1);
                yield ("delta", json!({"text": UNAVAILABLE_TEMPLATE}), Some("unavailable"));
                return;
            }
        };
        let policy = OutputPolicy {
            canary: s.prompt.canary.clone(),
            allowed_citations: Default::default(),
            allowed_domains: s.cfg.link_allowlist.clone(),
            may_see_pii,
        };
        let mut seg = Segmenter::default();
        let mut report = OutputReport::default();
        let mut proposals = 0;
        let mut said_anything = false;
        loop {
            let msg = match events.message().await {
                Ok(Some(m)) => m,
                Ok(None) => break,
                Err(e) => {
                    tracing::warn!(error = %e, "brain stream broke");
                    yield ("delta", json!({"text": UNAVAILABLE_TEMPLATE}), Some("unavailable"));
                    return;
                }
            };
            let pieces: Vec<String> = match msg.event {
                Some(pb::agent_event::Event::Token(t)) => seg.push(&t.text),
                Some(pb::agent_event::Event::ToolStarted(t)) => {
                    yield ("tool", json!({"name": t.name, "phase": "started"}), None);
                    Vec::new()
                }
                Some(pb::agent_event::Event::ToolResult(t)) => {
                    counter!(names::ACTIONS, "tool" => t.name.clone(), "result" => if t.ok { "ok" } else { "denied" }).increment(1);
                    yield ("tool", json!({"name": t.name, "phase": "done", "ok": t.ok}), None);
                    Vec::new()
                }
                Some(pb::agent_event::Event::Proposal(p)) => {
                    proposals += 1;
                    let summary: Value = serde_json::from_str(&p.summary_json).unwrap_or(Value::Null);
                    yield ("proposal", json!({"proposal_id": p.proposal_id, "action": p.action, "summary": summary}), None);
                    Vec::new()
                }
                Some(pb::agent_event::Event::Final(_)) => seg.finish().into_iter().collect(),
                Some(pb::agent_event::Event::Error(e)) => {
                    tracing::warn!(message = %e.message, "agent error");
                    yield ("delta", json!({"text": UNAVAILABLE_TEMPLATE}), Some("unavailable"));
                    return;
                }
                None => Vec::new(),
            };
            for piece in pieces {
                match scan_segment(&piece, &policy, &mut report) {
                    Ok(clean) => {
                        let clean = if may_see_pii { pii.unmask(&clean) } else { clean };
                        said_anything |= !clean.trim().is_empty();
                        yield ("delta", json!({"text": clean}), None);
                    }
                    Err(Block::CanaryLeak) => {
                        counter!(names::GUARD_BLOCKS, "layer" => "output").increment(1);
                        yield ("replace", json!({"text": NO_ANSWER_TEMPLATE}), Some("no_answer"));
                        return;
                    }
                }
            }
        }
        if proposals > 0 {
            yield ("", Value::Null, Some("proposal"));
        } else if said_anything {
            yield ("", Value::Null, Some("answered"));
        } else {
            // The model ended its turn with no text and no proposal (the offline mock does this for
            // unscripted requests): end like any other no-answer rather than an empty "answered".
            yield ("delta", json!({"text": NO_ANSWER_TEMPLATE}), Some("no_answer"));
        }
    })
}
