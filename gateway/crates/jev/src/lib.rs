//! TypeSafe Jev ("System One") client: typed answers + calibrated probabilities, no text.
//!
//! Two calls per Q&A request:
//! - **router**: one request with `intent` (choice), `complexity` (choice), `prompt_injection` (noul);
//! - **gate**: one *batched* request with `rel_i` + `inj_i` nouls for every retrieved passage.
//!
//! Jev is a hosted, early-stage vendor dependency, so every failure mode maps to one error,
//! `Unavailable`, which the gateway turns into deterministic degraded mode: timeout
//! (800 ms), 429/529/5xx, transport errors, an open circuit breaker, or an exhausted budget.
//! Only PII-masked text is ever sent (Jev has no zero-data-retention outside enterprise plans).
//!
//! Types follow https://api.typesafe.ai/openapi.json (v0.2.0, fetched 2026-10-30).

use std::collections::BTreeMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::Duration;

use metrics::{counter, gauge};
use resilience::Breaker;
use serde::Deserialize;
use serde_json::{Map, Value, json};
use telemetry::names;
use tracing::{Instrument, info_span};

#[derive(Debug, thiserror::Error)]
pub enum JevError {
    /// Any reason to degrade (never shown to users).
    #[error("jev unavailable: {0}")]
    Unavailable(&'static str),
    /// 400/413/422: our request is wrong — a bug, not an outage.
    #[error("jev rejected request ({status}): {detail}")]
    BadRequest { status: u16, detail: String },
    #[error("malformed jev response: {0}")]
    Malformed(String),
}

#[derive(Clone, Debug)]
pub struct JevConfig {
    pub base_url: String,
    pub api_key: String,
    /// `jev-latest` until a key confirms a pinnable id (the plan was `jev-1.14.0`; see Limitation.md).
    pub model: String,
    pub timeout: Duration,
    /// USD per 1M input tokens (output is free).
    pub price_in_per_m: f64,
    /// Total budget in USD; warn at 80%, refuse (degrade) at 95%.
    pub budget_usd: f64,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, serde::Serialize, serde::Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum Intent {
    PolicyQa,
    DataLookup,
    CreateRequest,
    Approve,
    OutOfScope,
}

impl Intent {
    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "policy_qa" => Intent::PolicyQa,
            "data_lookup" => Intent::DataLookup,
            "create_request" => Intent::CreateRequest,
            "approve" => Intent::Approve,
            "out_of_scope" => Intent::OutOfScope,
            _ => return None,
        })
    }
    pub fn as_str(self) -> &'static str {
        match self {
            Intent::PolicyQa => "policy_qa",
            Intent::DataLookup => "data_lookup",
            Intent::CreateRequest => "create_request",
            Intent::Approve => "approve",
            Intent::OutOfScope => "out_of_scope",
        }
    }
    /// Action intents go to the agent (brain) instead of the RAG fast path.
    pub fn is_action(self) -> bool {
        matches!(
            self,
            Intent::DataLookup | Intent::CreateRequest | Intent::Approve
        )
    }
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct RouteDecision {
    pub intent: Intent,
    pub intent_confidence: f64,
    pub intent_probabilities: BTreeMap<String, f64>,
    pub p_expensive: f64,
    pub p_injection: f64,
    pub model: String,
}

#[derive(Clone, Debug)]
pub struct GateScores {
    pub relevance: Vec<f64>,
    pub injection: Vec<f64>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BudgetStatus {
    Ok,
    Warn,
    Exhausted,
}

/// Spend tracker in integer micro-dollars. In-process; the api crate seeds it from Valkey on
/// start-up and persists increments so restarts don't reset the meter.
#[derive(Debug)]
pub struct BudgetMeter {
    budget_micros: u64,
    spent_micros: AtomicU64,
}

impl BudgetMeter {
    pub fn new(budget_usd: f64, already_spent_micros: u64) -> Self {
        Self {
            budget_micros: (budget_usd * 1e6) as u64,
            spent_micros: AtomicU64::new(already_spent_micros),
        }
    }
    pub fn add(&self, micros: u64) -> u64 {
        self.spent_micros.fetch_add(micros, Ordering::Relaxed) + micros
    }
    pub fn spent_micros(&self) -> u64 {
        self.spent_micros.load(Ordering::Relaxed)
    }
    pub fn status(&self) -> BudgetStatus {
        if self.budget_micros == 0 {
            return BudgetStatus::Ok;
        }
        let used = self.spent_micros() as f64 / self.budget_micros as f64;
        if used >= 0.95 {
            BudgetStatus::Exhausted
        } else if used >= 0.80 {
            BudgetStatus::Warn
        } else {
            BudgetStatus::Ok
        }
    }
}

/// The versioned question sets from prompts/*.json.
#[derive(Clone, Debug)]
pub struct QuestionSets {
    pub router: Value,
    pub router_version: u32,
    gate_relevance: Value,
    gate_injection: Value,
    pub gate_version: u32,
}

impl QuestionSets {
    pub fn from_json(router_json: &str, gate_json: &str) -> Result<Self, JevError> {
        let r: Value =
            serde_json::from_str(router_json).map_err(|e| JevError::Malformed(e.to_string()))?;
        let g: Value =
            serde_json::from_str(gate_json).map_err(|e| JevError::Malformed(e.to_string()))?;
        Ok(Self {
            router: r["questions"].clone(),
            router_version: r["version"].as_u64().unwrap_or(0) as u32,
            gate_relevance: g["relevance"].clone(),
            gate_injection: g["injection"].clone(),
            gate_version: g["version"].as_u64().unwrap_or(0) as u32,
        })
    }

    fn with_passage(template: &Value, passage: &str) -> Value {
        let mut q = template.clone();
        if let Some(p) = q.pointer_mut("/instructions/passage") {
            *p = Value::String(passage.to_owned());
        }
        q
    }

    /// `rel_0..rel_{n-1}` and `inj_0..` questions for one batched gate request.
    pub fn gate_questions(&self, passages: &[String]) -> Value {
        let mut qs = Map::new();
        for (i, p) in passages.iter().enumerate() {
            qs.insert(
                format!("rel_{i}"),
                Self::with_passage(&self.gate_relevance, p),
            );
            qs.insert(
                format!("inj_{i}"),
                Self::with_passage(&self.gate_injection, p),
            );
        }
        Value::Object(qs)
    }
}

#[derive(Deserialize)]
struct Usage {
    input_tokens: u64,
}

#[derive(Deserialize)]
struct SystemOneResponse {
    model: String,
    answers: Map<String, Value>,
    usage: Usage,
}

pub struct JevClient {
    http: reqwest::Client,
    cfg: JevConfig,
    pub questions: QuestionSets,
    pub breaker: Breaker,
    pub budget: BudgetMeter,
}

impl JevClient {
    pub fn new(cfg: JevConfig, questions: QuestionSets, already_spent_micros: u64) -> Self {
        let http = reqwest::Client::builder()
            .connect_timeout(Duration::from_millis(500))
            .build()
            .expect("reqwest client");
        let budget = BudgetMeter::new(cfg.budget_usd, already_spent_micros);
        Self {
            http,
            cfg,
            questions,
            breaker: Breaker::new(3, Duration::from_secs(30)),
            budget,
        }
    }

    pub fn configured(&self) -> bool {
        !self.cfg.api_key.is_empty()
    }

    /// Is Jev usable right now? (Used for the admin "reduced mode" badge and `jev_degraded`.)
    pub fn available(&self) -> bool {
        self.configured()
            && self.budget.status() != BudgetStatus::Exhausted
            && self.breaker.state() != resilience::State::Open
    }

    async fn ask(
        &self,
        kind: &'static str,
        state: Value,
        questions: Value,
    ) -> Result<(String, Map<String, Value>), JevError> {
        if !self.configured() {
            return Err(JevError::Unavailable("no_api_key"));
        }
        if self.budget.status() == BudgetStatus::Exhausted {
            counter!(names::JEV_REQUESTS, "kind" => kind, "result" => "budget").increment(1);
            return Err(JevError::Unavailable("budget"));
        }
        if !self.breaker.allow() {
            counter!(names::JEV_REQUESTS, "kind" => kind, "result" => "breaker_open").increment(1);
            return Err(JevError::Unavailable("breaker_open"));
        }
        let url = format!("{}/v1/systemone", self.cfg.base_url.trim_end_matches('/'));
        let body = json!({"model": self.cfg.model, "state": state, "questions": questions});
        let span = info_span!("jev.call", kind, model = %self.cfg.model);
        let result = self
            .http
            .post(url)
            .bearer_auth(&self.cfg.api_key)
            .timeout(self.cfg.timeout)
            .json(&body)
            .send()
            .instrument(span)
            .await;
        let resp = match result {
            Ok(r) => r,
            Err(e) => {
                self.breaker.on_failure();
                let reason = if e.is_timeout() {
                    "timeout"
                } else {
                    "transport"
                };
                counter!(names::JEV_REQUESTS, "kind" => kind, "result" => reason).increment(1);
                return Err(JevError::Unavailable(reason));
            }
        };
        let status = resp.status().as_u16();
        if !(200..300).contains(&status) {
            let reason: &'static str = match status {
                429 => "rate_limited",
                529 => "overloaded",
                401 | 403 => "auth",
                s if s >= 500 => "upstream_5xx",
                _ => "bad_request",
            };
            counter!(names::JEV_REQUESTS, "kind" => kind, "result" => reason).increment(1);
            if reason == "bad_request" {
                let detail = resp
                    .text()
                    .await
                    .unwrap_or_default()
                    .chars()
                    .take(300)
                    .collect();
                return Err(JevError::BadRequest { status, detail });
            }
            if reason == "auth" {
                self.breaker.trip();
            } else {
                self.breaker.on_failure();
            }
            return Err(JevError::Unavailable(reason));
        }
        let parsed: SystemOneResponse = match resp.json().await {
            Ok(p) => p,
            Err(e) => {
                self.breaker.on_failure();
                return Err(JevError::Malformed(e.to_string()));
            }
        };
        self.breaker.on_success();
        let micros = (parsed.usage.input_tokens as f64 * self.cfg.price_in_per_m) as u64; // per-M price * tokens / 1e6 * 1e6
        let total = self.budget.add(micros);
        counter!(names::JEV_REQUESTS, "kind" => kind, "result" => "ok").increment(1);
        counter!(names::JEV_COST_USD_MICROS).increment(micros);
        gauge!("jev_budget_spent_usd").set(total as f64 / 1e6);
        Ok((parsed.model, parsed.answers))
    }

    pub async fn route(
        &self,
        masked_query: &str,
        recent_turns: &[String],
    ) -> Result<RouteDecision, JevError> {
        let state = json!({"query": masked_query, "recent_turns": recent_turns});
        let (model, answers) = self
            .ask("route", state, self.questions.router.clone())
            .await?;
        let intent_a = answers
            .get("intent")
            .ok_or_else(|| JevError::Malformed("no intent".into()))?;
        let intent = intent_a["choice"]
            .as_str()
            .and_then(Intent::parse)
            .ok_or_else(|| JevError::Malformed("intent".into()))?;
        let probs = |a: &Value| -> BTreeMap<String, f64> {
            a["probabilities"]
                .as_object()
                .map(|m| {
                    m.iter()
                        .filter_map(|(k, v)| v.as_f64().map(|p| (k.clone(), p)))
                        .collect()
                })
                .unwrap_or_default()
        };
        let complexity = answers
            .get("complexity")
            .ok_or_else(|| JevError::Malformed("no complexity".into()))?;
        Ok(RouteDecision {
            intent,
            intent_confidence: intent_a["confidence"].as_f64().unwrap_or(0.0),
            intent_probabilities: probs(intent_a),
            p_expensive: probs(complexity).get("expensive").copied().unwrap_or(1.0),
            p_injection: answers
                .get("prompt_injection")
                .and_then(|a| a["noul"].as_f64())
                .unwrap_or(0.0),
            model,
        })
    }

    pub async fn gate(
        &self,
        masked_query: &str,
        passages: &[String],
    ) -> Result<GateScores, JevError> {
        if passages.is_empty() {
            return Ok(GateScores {
                relevance: vec![],
                injection: vec![],
            });
        }
        let qs = self.questions.gate_questions(passages);
        let (_, answers) = self.ask("gate", json!({"query": masked_query}), qs).await?;
        let get = |name: String| {
            answers
                .get(&name)
                .and_then(|a| a["noul"].as_f64())
                .ok_or_else(|| JevError::Malformed(format!("missing {name}")))
        };
        let mut relevance = Vec::with_capacity(passages.len());
        let mut injection = Vec::with_capacity(passages.len());
        for i in 0..passages.len() {
            relevance.push(get(format!("rel_{i}"))?);
            injection.push(get(format!("inj_{i}"))?);
        }
        Ok(GateScores {
            relevance,
            injection,
        })
    }

    /// Groundedness judge for evals: "every claim is supported by the passages".
    pub async fn groundedness(&self, answer: &str, passages: &[String]) -> Result<f64, JevError> {
        let qs = json!({"grounded": {"type": "noul",
            "instructions": "Every claim in the answer is supported by the passages.",
            "criteria": {"true": "each factual statement in the answer is stated in or directly implied by the passages",
                         "false": "at least one statement is missing from, or contradicts, the passages"}}});
        let (_, answers) = self
            .ask("judge", json!({"answer": answer, "passages": passages}), qs)
            .await?;
        answers
            .get("grounded")
            .and_then(|a| a["noul"].as_f64())
            .ok_or_else(|| JevError::Malformed("grounded".into()))
    }
}

/// Gate decision rule: keep relevance >= tau_rel, drop injection >= tau_inj, best first,
/// cap at `max_keep`. Returns the indices of kept passages.
pub fn select(scores: &GateScores, tau_rel: f64, tau_inj: f64, max_keep: usize) -> Vec<usize> {
    let mut kept: Vec<usize> = (0..scores.relevance.len())
        .filter(|&i| scores.relevance[i] >= tau_rel && scores.injection[i] < tau_inj)
        .collect();
    kept.sort_by(|&a, &b| scores.relevance[b].total_cmp(&scores.relevance[a]));
    kept.truncate(max_keep);
    kept
}

#[cfg(test)]
mod tests;
