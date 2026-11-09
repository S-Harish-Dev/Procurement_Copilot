//! Admin Insights data, shaped for the charts in `docs/api.md`.
//!
//! - `/admin/metrics/overview`: PromQL against the Prometheus that scrapes this gateway. The
//!   numbers come from the same counters Grafana shows, so the two never disagree.
//! - `/admin/evals`, `/admin/experiments`: the JSON reports the eval/ablation scripts write.
//! - `/admin/traces`: the in-memory request ring (`crate::traces`).

use std::collections::BTreeMap;
use std::path::Path;
use std::sync::OnceLock;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use axum::Json;
use axum::extract::{Path as UrlPath, Query, State};
use axum::http::StatusCode;
use axum::response::{IntoResponse, Response};
use futures::future::join_all;
use serde::Deserialize;
use serde_json::{Map, Value, json};

use crate::http::{AdminUser, ApiError};
use crate::state::SharedState;

/// Red-team categories the input guard is supposed to *block* (the others are stopped downstream
/// by ACL, masking and confirmation): the positives of the injection-detector confusion matrix.
const GUARD_CATEGORIES: [&str; 4] = [
    "direct_injection",
    "prompt_extraction",
    "jailbreak_roleplay",
    "canary_extraction",
];

fn now_rfc3339() -> String {
    time::OffsetDateTime::now_utc()
        .format(&time::format_description::well_known::Rfc3339)
        .unwrap_or_default()
}

fn unix_now() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map_or(0.0, |d| d.as_secs_f64())
}

// ---- Prometheus ------------------------------------------------------------------------------

#[derive(Debug, thiserror::Error)]
#[error("prometheus: {0}")]
struct PromError(String);

type Labels = BTreeMap<String, String>;

struct Prom {
    http: &'static reqwest::Client,
    base: String,
    end: f64,
    range: u64,
    step: u64,
}

fn client() -> &'static reqwest::Client {
    static C: OnceLock<reqwest::Client> = OnceLock::new();
    C.get_or_init(|| {
        reqwest::Client::builder()
            .timeout(Duration::from_secs(5))
            .build()
            .unwrap_or_default()
    })
}

fn num(v: &Value) -> Option<f64> {
    v.as_str()
        .and_then(|s| s.parse::<f64>().ok())
        .filter(|x| x.is_finite())
}

/// Expression templates use `[W]` for the window; this fills in the window and an optional offset
/// (`[W]` -> `[3600s] offset 3600s`) so one template serves value, previous period and series.
fn fill(template: &str, window: u64, offset: u64) -> String {
    let off = if offset > 0 {
        format!(" offset {offset}s")
    } else {
        String::new()
    };
    template.replace("[W]", &format!("[{window}s]{off}"))
}

impl Prom {
    async fn get(&self, path: &str, params: &[(&str, String)]) -> Result<Vec<Value>, PromError> {
        let url = reqwest::Url::parse_with_params(&format!("{}{path}", self.base), params)
            .map_err(|e| PromError(e.to_string()))?;
        let resp = self
            .http
            .get(url)
            .send()
            .await
            .map_err(|e| PromError(e.to_string()))?;
        let body: Value = resp.json().await.map_err(|e| PromError(e.to_string()))?;
        if body["status"] != "success" {
            return Err(PromError(
                body["error"].as_str().unwrap_or("query failed").into(),
            ));
        }
        Ok(body["data"]["result"]
            .as_array()
            .cloned()
            .unwrap_or_default())
    }

    fn labels(v: &Value) -> Labels {
        v["metric"]
            .as_object()
            .map(|m| {
                m.iter()
                    .map(|(k, v)| (k.clone(), v.as_str().unwrap_or("").to_owned()))
                    .collect()
            })
            .unwrap_or_default()
    }

    /// Instant vector at the end of the range.
    async fn vector(&self, q: String) -> Result<Vec<(Labels, f64)>, PromError> {
        let rows = self
            .get(
                "/api/v1/query",
                &[("query", q), ("time", self.end.to_string())],
            )
            .await?;
        Ok(rows
            .iter()
            .filter_map(|r| num(&r["value"][1]).map(|v| (Self::labels(r), v)))
            .collect())
    }

    /// Sum of the instant vector; `None` when there is no data (not zero: "no data" ≠ "zero").
    async fn scalar(&self, q: String) -> Result<Option<f64>, PromError> {
        let v = self.vector(q).await?;
        Ok((!v.is_empty()).then(|| v.iter().map(|(_, x)| x).sum()))
    }

    async fn matrix(&self, q: String) -> Result<Vec<(Labels, Vec<[f64; 2]>)>, PromError> {
        let rows = self
            .get(
                "/api/v1/query_range",
                &[
                    ("query", q),
                    ("start", (self.end - self.range as f64).to_string()),
                    ("end", self.end.to_string()),
                    ("step", self.step.to_string()),
                ],
            )
            .await?;
        Ok(rows
            .iter()
            .map(|r| {
                let pts = r["values"]
                    .as_array()
                    .map(|vs| {
                        vs.iter()
                            .filter_map(|p| Some([p[0].as_f64()?, num(&p[1])?]))
                            .collect()
                    })
                    .unwrap_or_default();
                (Self::labels(r), pts)
            })
            .collect())
    }

    async fn series(&self, template: &str) -> Result<Vec<[f64; 2]>, PromError> {
        Ok(self
            .matrix(fill(template, self.step, 0))
            .await?
            .into_iter()
            .next()
            .map(|(_, s)| s)
            .unwrap_or_default())
    }

    async fn by_label(&self, template: &str, label: &str) -> Result<Vec<(String, f64)>, PromError> {
        let mut rows: Vec<(String, f64)> = self
            .vector(fill(template, self.range, 0))
            .await?
            .into_iter()
            .map(|(l, v)| (l.get(label).cloned().unwrap_or_default(), v))
            .collect();
        rows.sort_by(|a, b| b.1.total_cmp(&a.1));
        Ok(rows)
    }

    /// Histogram buckets as non-cumulative counts per `le`.
    async fn buckets(&self, metric: &str) -> Result<Vec<Value>, PromError> {
        let q = fill(
            &format!("sum by (le) (increase({metric}_bucket[W]))"),
            self.range,
            0,
        );
        let mut rows: Vec<(f64, String, f64)> = self
            .vector(q)
            .await?
            .into_iter()
            .map(|(l, v)| {
                let le = l.get("le").cloned().unwrap_or_default();
                let key = if le == "+Inf" {
                    f64::INFINITY
                } else {
                    le.parse().unwrap_or(f64::INFINITY)
                };
                (key, le, v)
            })
            .collect();
        rows.sort_by(|a, b| a.0.total_cmp(&b.0));
        let mut prev = 0.0;
        Ok(rows
            .into_iter()
            .map(|(_, le, cum)| {
                let count = (cum - prev).max(0.0);
                prev = cum;
                json!({"le": le, "count": count.round()})
            })
            .collect())
    }
}

const ABSTAINED: &str = r#"reason=~"NoMatch|OutOfScope|ModelAbstained""#;

/// `(id, label, unit, template)`: every KPI is one PromQL template over the window `[W]`.
fn kpi_templates() -> Vec<(&'static str, &'static str, &'static str, String)> {
    vec![
        ("requests", "Requests", "count", "sum(increase(chat_requests_total[W]))".into()),
        (
            "p95_latency_s",
            "p95 latency",
            "seconds",
            r#"histogram_quantile(0.95, sum by (le) (increase(http_request_duration_seconds_bucket{route="/v1/chat"}[W])))"#.into(),
        ),
        (
            "cache_hit_rate",
            "Answers from cache",
            "ratio",
            r#"sum(increase(answers_total{source="cache"}[W])) / sum(increase(answers_total[W]))"#.into(),
        ),
        (
            "cost_actual_usd",
            "Actual cost",
            "usd",
            r#"((sum(increase(llm_cost_usd_micros_total{kind="actual"}[W])) or vector(0)) + (sum(increase(jev_cost_usd_micros_total[W])) or vector(0))) / 1e6"#.into(),
        ),
        (
            // What the same answers would have cost with no cache and no router: every answer at the
            // expensive tier, priced at the average shadow cost of the answers that did reach an LLM.
            "cost_shadow_usd",
            "Cost without cache & router",
            "usd",
            r#"sum(increase(llm_cost_usd_micros_total{kind="shadow"}[W])) / sum(increase(answers_total{source="llm"}[W])) * sum(increase(answers_total[W])) / 1e6"#.into(),
        ),
        ("guard_blocks", "Blocked attempts", "count", "sum(increase(guard_blocks_total[W]))".into()),
        ("jev_degraded_ratio", "Time in reduced mode", "ratio", "avg_over_time(jev_degraded[W])".into()),
        (
            "abstention_rate",
            "No-answer rate",
            "ratio",
            format!("sum(increase(abstentions_total{{{ABSTAINED}}}[W])) / sum(increase(chat_requests_total[W]))"),
        ),
    ]
}

const STAGES: [&str; 12] = [
    "ratelimit",
    "guard",
    "jev.route",
    "policy.opa",
    "cache.lookup",
    "embed",
    "cache.semantic",
    "retrieve.hybrid",
    "jev.gate",
    "llm.first_segment",
    "llm.total",
    "agent",
];

#[derive(Deserialize)]
pub struct RangeQuery {
    range: Option<String>,
}

fn range_secs(r: &str) -> Option<u64> {
    Some(match r {
        "1h" => 3_600,
        "6h" => 6 * 3_600,
        "24h" => 24 * 3_600,
        "7d" => 7 * 24 * 3_600,
        _ => return None,
    })
}

pub async fn overview(
    State(s): State<SharedState>,
    _admin: AdminUser,
    Query(q): Query<RangeQuery>,
) -> Response {
    let label = q.range.unwrap_or_else(|| "24h".into());
    let Some(range) = range_secs(&label) else {
        return ApiError::BadRequest("range must be 1h, 6h, 24h or 7d.".into()).into_response();
    };
    let prom = Prom {
        http: client(),
        base: s.cfg.prometheus_url.trim_end_matches('/').to_owned(),
        end: unix_now().floor(),
        range,
        // ~60 points per sparkline, never finer than the 15 s scrape interval.
        step: (range / 60).max(15),
    };
    match build_overview(&prom, &label).await {
        Ok(v) => Json(v).into_response(),
        Err(e) => {
            tracing::warn!(error = %e, "insights overview: prometheus unavailable");
            (
                StatusCode::SERVICE_UNAVAILABLE,
                Json(json!({"error": "prometheus_unavailable"})),
            )
                .into_response()
        }
    }
}

async fn build_overview(p: &Prom, label: &str) -> Result<Value, PromError> {
    let templates = kpi_templates();
    let kpis = join_all(templates.iter().map(|(id, name, unit, t)| async move {
        let (value, previous, series) = tokio::join!(
            p.scalar(fill(t, p.range, 0)),
            p.scalar(fill(t, p.range, p.range)),
            p.series(t),
        );
        Ok::<Value, PromError>(json!({
            "id": id, "label": name, "unit": unit,
            "value": value?, "previous": previous?, "series": series?,
        }))
    }))
    .await
    .into_iter()
    .collect::<Result<Vec<_>, _>>()?;

    let total = |t: &str| p.scalar(fill(t, p.range, 0));
    let (received, blocked, cache_hit, retrieved, answered, abstained) = tokio::join!(
        total("sum(increase(chat_requests_total[W]))"),
        total(r#"sum(increase(abstentions_total{reason=~"BlockedInput|Locked"}[W]))"#),
        total(r#"sum(increase(answers_total{source="cache"}[W]))"#),
        total(r#"sum(increase(stage_duration_seconds_count{stage="retrieve.hybrid"}[W]))"#),
        total("sum(increase(answers_total[W]))"),
        total(&format!(
            "sum(increase(abstentions_total{{{ABSTAINED}}}[W]))"
        )),
    );
    let (received, blocked) = (received?.unwrap_or(0.0), blocked?.unwrap_or(0.0));
    let funnel = json!([
        {"stage": "received", "count": received.round()},
        {"stage": "passed_guard", "count": (received - blocked).max(0.0).round()},
        {"stage": "cache_hit", "count": cache_hit?.unwrap_or(0.0).round()},
        {"stage": "retrieved", "count": retrieved?.unwrap_or(0.0).round()},
        {"stage": "answered", "count": answered?.unwrap_or(0.0).round()},
        {"stage": "abstained", "count": abstained?.unwrap_or(0.0).round()},
        {"stage": "blocked", "count": blocked.round()},
    ]);

    let quantile = |q: f64| {
        p.vector(fill(
            &format!("histogram_quantile({q}, sum by (le, stage) (increase(stage_duration_seconds_bucket[W])))"),
            p.range,
            0,
        ))
    };
    let (p50, p95) = tokio::join!(quantile(0.5), quantile(0.95));
    let (p50, p95) = (p50?, p95?);
    let at = |rows: &[(Labels, f64)], stage: &str| {
        rows.iter()
            .find(|(l, _)| l.get("stage").map(String::as_str) == Some(stage))
            .map(|(_, v)| (v * 1e3 * 100.0).round() / 100.0)
    };
    let latency_stages: Vec<Value> = STAGES
        .iter()
        .filter_map(|st| {
            let (a, b) = (at(&p50, st), at(&p95, st));
            (a.is_some() || b.is_some()).then(|| json!({"stage": st, "p50_ms": a, "p95_ms": b}))
        })
        .collect();

    let named = |rows: Vec<(String, f64)>, key: &str, val: &str| -> Value {
        rows.into_iter()
            .map(|(k, v)| json!({key: k, val: v.round()}))
            .collect()
    };
    let (intent, tier, providers, guard) = tokio::join!(
        p.by_label(
            "sum by (intent) (increase(route_decisions_total[W]))",
            "intent"
        ),
        p.by_label("sum by (tier) (increase(route_decisions_total[W]))", "tier"),
        p.by_label(
            r#"sum by (provider) (increase(llm_requests_total{result="ok"}[W]))"#,
            "provider"
        ),
        p.by_label("sum by (layer) (increase(guard_blocks_total[W]))", "layer"),
    );
    let fallbacks: Vec<Value> = p
        .vector(fill(
            "sum by (from, to) (increase(llm_fallback_total[W]))",
            p.range,
            0,
        ))
        .await?
        .into_iter()
        .map(|(l, v)| json!({"from": l.get("from"), "to": l.get("to"), "count": v.round()}))
        .collect();

    let shadow_per_answer = format!(
        r#"scalar(sum(increase(llm_cost_usd_micros_total{{kind="shadow"}}[{r}s])) / sum(increase(answers_total{{source="llm"}}[{r}s])))"#,
        r = p.range
    );
    let shadow_series = format!("sum(increase(answers_total[W])) * {shadow_per_answer} / 1e6");
    let (fallback_series, actual, shadow, cache_layers, computed, gate_kept, gate_relevance) = tokio::join!(
        p.series("sum(increase(llm_fallback_total[W]))"),
        p.series(r#"((sum(increase(llm_cost_usd_micros_total{kind="actual"}[W])) or vector(0)) + (sum(increase(jev_cost_usd_micros_total[W])) or vector(0))) / 1e6"#),
        p.series(&shadow_series),
        p.matrix(fill(
            r#"sum by (layer) (increase(cache_requests_total{cache="answer", result="hit"}[W]))"#,
            p.step,
            0
        )),
        p.series(r#"sum(increase(answers_total{source="llm"}[W]))"#),
        p.buckets("gate_kept_chunks"),
        p.buckets("gate_relevance"),
    );
    let mut cache_series = Map::new();
    for layer in ["l1", "l2", "semantic"] {
        cache_series.insert(layer.into(), json!([]));
    }
    for (l, pts) in cache_layers? {
        if let Some(layer) = l.get("layer") {
            cache_series.insert(layer.clone(), json!(pts));
        }
    }
    cache_series.insert("miss".into(), json!(computed?));

    Ok(json!({
        "range": label,
        "step_seconds": p.step,
        "generated_at": now_rfc3339(),
        "source": "prometheus",
        "kpis": kpis,
        "funnel": funnel,
        "latency_stages": latency_stages,
        "router": {"by_intent": named(intent?, "intent", "count"), "by_tier": named(tier?, "tier", "count")},
        "providers": {
            "usage": named(providers?, "provider", "requests"),
            "fallbacks": fallbacks,
            "fallback_series": fallback_series?,
        },
        "cost_series": {"actual": actual?, "shadow": shadow?},
        "cache_series": cache_series,
        "gate_kept": gate_kept?,
        "gate_relevance": gate_relevance?,
        "guard_blocks": named(guard?, "layer", "count"),
    }))
}

// ---- Evals & experiments (files written by the eval scripts) -----------------------------------

fn read_json(path: &Path) -> Option<Value> {
    serde_json::from_str(&std::fs::read_to_string(path).ok()?).ok()
}

/// `(run_id, report)` for every `evals/reports/*-{suite}.json`, oldest first (ids start with a UTC timestamp).
fn reports(dir: &Path, suite: &str) -> Vec<(String, Value)> {
    let suffix = format!("-{suite}.json");
    let mut out: Vec<(String, Value)> = std::fs::read_dir(dir)
        .into_iter()
        .flatten()
        .flatten()
        .filter_map(|e| {
            let name = e.file_name().to_string_lossy().into_owned();
            let id = name.strip_suffix(".json")?.to_owned();
            name.ends_with(&suffix)
                .then(|| read_json(&e.path()).map(|v| (id, v)))
                .flatten()
        })
        .collect();
    out.sort_by(|a, b| a.0.cmp(&b.0));
    out
}

/// "20261030T165557Z-fast" -> "2026-10-30T16:55:57Z".
fn run_time(run_id: &str) -> Option<String> {
    let t = run_id.get(..16)?;
    Some(format!(
        "{}-{}-{}T{}:{}:{}Z",
        t.get(0..4)?,
        t.get(4..6)?,
        t.get(6..8)?,
        t.get(9..11)?,
        t.get(11..13)?,
        t.get(13..15)?
    ))
}

fn summarize_fast(id: &str, r: &Value) -> Value {
    json!({
        "run_id": id,
        "created_at": run_time(id),
        "git_sha": r["git_sha"],
        "label": r["label"],
        "passed": r["passed"],
        "n_cases": r["dataset"]["n_cases"].as_u64().or_else(|| r["dataset"]["cases"].as_u64()),
        "metrics": r["metrics"],
        "hard_gates": r["hard_gates"],
    })
}

fn summarize_redteam(id: &str, r: &Value) -> Value {
    let rates = &r["block_rate_by_category"];
    let (mut tp, mut fneg) = (0.0, 0.0);
    for c in GUARD_CATEGORIES {
        let blocked = rates[c]["blocked"].as_f64().unwrap_or(0.0);
        tp += blocked;
        fneg += rates[c]["n"].as_f64().unwrap_or(0.0) - blocked;
    }
    let fp = r["benign"]["false_positives"].as_f64().unwrap_or(0.0);
    let tn = r["benign"]["n"].as_f64().unwrap_or(0.0) - fp;
    json!({
        "run_id": id,
        "created_at": run_time(id),
        "passed": r["passed"],
        "hard_gates": r["hard_gates"],
        "block_rates": rates,
        "blocks_by_layer": r["blocks_by_layer"],
        "benign_fpr": r["benign"]["fpr"],
        "timing": r["timing"],
        "confusion": {"tp": tp, "fn": fneg, "fp": fp, "tn": tn},
        "note": r["note"],
    })
}

pub async fn evals(State(s): State<SharedState>, _admin: AdminUser) -> Json<Value> {
    let dir = s.cfg.repo_root.join("evals/reports");
    let fast = reports(&dir, "fast");
    let latest = |suite: &str| reports(&dir, suite).pop();
    let baseline = read_json(&s.cfg.repo_root.join("evals/baselines/baseline.json"));
    Json(json!({
        "generated_at": now_rfc3339(),
        "source": "evals/reports",
        "runs": fast.iter().map(|(id, r)| summarize_fast(id, r)).collect::<Vec<_>>(),
        "baseline": baseline.map(|b| json!({"run_id": b["run_id"], "label": b["label"], "metrics": b["metrics"]})),
        "tolerances": fast.last().map(|(_, r)| r["tolerances"].clone()).unwrap_or(Value::Null),
        "redteam": latest("redteam").map(|(id, r)| summarize_redteam(&id, &r)),
        "cache_check": latest("cache").map(|(id, r)| json!({
            "run_id": id, "created_at": run_time(&id), "passed": r["passed"], "hard_gates": r["hard_gates"],
            "isolation": {"probes": r["isolation"]["probes"], "owner_repeat_hit_rate": r["isolation"]["owner_repeat_hit_rate"]},
            "semantic": {"semantic_hit_rate": r["semantic"]["semantic_hit_rate"], "answered_originals": r["semantic"]["answered_originals"]},
            "index_bump": r["index_bump"],
        })),
        "agent_tasks": latest("agent").map(|(id, r)| json!({
            "run_id": id, "created_at": run_time(&id), "passed": r["passed"], "summary": r["summary"],
        })),
        // Jev vs Gemini judge agreement needs real-LLM eval-full runs (API keys).
        "judges": Value::Null,
    }))
}

pub async fn eval_run(
    State(s): State<SharedState>,
    _admin: AdminUser,
    UrlPath(run_id): UrlPath<String>,
) -> Result<Json<Value>, ApiError> {
    // Ids are generated as <UTC timestamp>-<suite>; anything else (../, slashes) is rejected.
    if !run_id
        .chars()
        .all(|c| c.is_ascii_alphanumeric() || c == '-')
        || run_id.len() > 64
    {
        return Err(ApiError::NotFound);
    }
    read_json(&s.cfg.repo_root.join(format!("evals/reports/{run_id}.json")))
        .map(Json)
        .ok_or(ApiError::NotFound)
}

pub async fn experiments(State(s): State<SharedState>, _admin: AdminUser) -> Json<Value> {
    let dir = s.cfg.repo_root.join("evals/experiments");
    let mut items: Vec<Value> = std::fs::read_dir(&dir)
        .into_iter()
        .flatten()
        .flatten()
        .filter(|e| e.path().extension().is_some_and(|x| x == "json"))
        .filter_map(|e| read_json(&e.path()))
        .collect();
    items.sort_by(|a, b| a["id"].as_str().cmp(&b["id"].as_str()));
    Json(
        json!({"generated_at": now_rfc3339(), "source": "evals/experiments", "experiments": items}),
    )
}

// ---- Traces ------------------------------------------------------------------------------------

#[derive(Deserialize)]
pub struct Limit {
    limit: Option<usize>,
}

pub async fn traces(
    State(s): State<SharedState>,
    _admin: AdminUser,
    Query(q): Query<Limit>,
) -> Json<Value> {
    let items = s.traces.recent(q.limit.unwrap_or(50).min(1_000));
    Json(json!({"generated_at": now_rfc3339(), "source": "gateway ring buffer", "items": items}))
}

pub async fn trace(
    State(s): State<SharedState>,
    _admin: AdminUser,
    UrlPath(id): UrlPath<String>,
) -> Result<Json<Value>, ApiError> {
    let r = s.traces.find(&id).ok_or(ApiError::NotFound)?;
    let grafana_url = match (&s.cfg.grafana_url, &r.trace_id) {
        (Some(g), Some(tid)) if tid.chars().all(|c| c.is_ascii_hexdigit()) => Some(format!(
            "{}/explore?left=%7B%22datasource%22%3A%22tempo%22%2C%22queries%22%3A%5B%7B%22refId%22%3A%22A%22%2C%22queryType%22%3A%22traceql%22%2C%22query%22%3A%22{tid}%22%7D%5D%7D",
            g.trim_end_matches('/')
        )),
        _ => None,
    };
    Ok(Json(json!({
        "request_id": r.request_id,
        "trace_id": r.trace_id,
        "at": r.at.format(&time::format_description::well_known::Rfc3339).unwrap_or_default(),
        "status": r.status,
        "role": r.role,
        "total_ms": r.total_ms,
        "spans": r.spans,
        "diagnostics": r.diagnostics,
        "grafana_url": grafana_url,
    })))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn templates_fill_window_and_offset() {
        assert_eq!(fill("sum(increase(x[W]))", 60, 0), "sum(increase(x[60s]))");
        assert_eq!(
            fill("a[W] / b[W]", 3600, 3600),
            "a[3600s] offset 3600s / b[3600s] offset 3600s"
        );
    }

    #[test]
    fn run_ids_become_timestamps() {
        assert_eq!(
            run_time("20261030T165557Z-fast").as_deref(),
            Some("2026-10-30T16:55:57Z")
        );
        assert_eq!(run_time("bad"), None);
    }

    #[test]
    fn confusion_counts_only_guard_categories() {
        let r = json!({
            "block_rate_by_category": {
                "direct_injection": {"blocked": 9, "n": 10},
                "canary_extraction": {"blocked": 3, "n": 3},
                "cross_role_exfil": {"blocked": 0, "n": 20}
            },
            "benign": {"n": 150, "false_positives": 1, "fpr": 0.0067}
        });
        let s = summarize_redteam("x", &r);
        assert_eq!(
            s["confusion"],
            json!({"tp": 12.0, "fn": 1.0, "fp": 1.0, "tn": 149.0})
        );
    }
}
