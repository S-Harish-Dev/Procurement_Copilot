//! Tracing, OpenTelemetry export and Prometheus metrics for the gateway.
//!
//! - Logs: JSON lines on stdout (collected into Loki in the compose stack), with trace/span ids.
//! - Traces: OTLP/gRPC to the collector when `OTEL_EXPORTER_OTLP_ENDPOINT` is set; W3C trace context
//!   is propagated to the brain over gRPC metadata so one trace spans both languages.
//! - Metrics: Prometheus text format, served by the api crate on the metrics port.
//!
//! Metric names live here as constants so dashboards, alerts and code agree on one spelling.

use anyhow::Context;
use metrics_exporter_prometheus::{Matcher, PrometheusBuilder, PrometheusHandle};
use opentelemetry::trace::TracerProvider as _;
use opentelemetry_otlp::WithExportConfig;
use opentelemetry_sdk::Resource;
use opentelemetry_sdk::propagation::TraceContextPropagator;
use opentelemetry_sdk::trace::SdkTracerProvider;
use tracing_subscriber::layer::SubscriberExt;
use tracing_subscriber::util::SubscriberInitExt;
use tracing_subscriber::{EnvFilter, Layer};

pub mod names {
    pub const HTTP_REQUEST_DURATION: &str = "http_request_duration_seconds";
    pub const STAGE_DURATION: &str = "stage_duration_seconds";
    pub const LLM_TOKENS: &str = "llm_tokens_total";
    /// Integer micro-dollars: `metrics` counters are u64. Dashboards divide by 1e6.
    pub const LLM_COST_USD_MICROS: &str = "llm_cost_usd_micros_total";
    pub const LLM_FALLBACK: &str = "llm_fallback_total";
    pub const LLM_REQUESTS: &str = "llm_requests_total";
    pub const JEV_REQUESTS: &str = "jev_requests_total";
    pub const JEV_COST_USD_MICROS: &str = "jev_cost_usd_micros_total";
    pub const JEV_DEGRADED: &str = "jev_degraded";
    pub const GATE_KEPT_CHUNKS: &str = "gate_kept_chunks";
    pub const CACHE_REQUESTS: &str = "cache_requests_total";
    pub const GUARD_BLOCKS: &str = "guard_blocks_total";
    pub const ABSTENTIONS: &str = "abstentions_total";
    /// Answered requests by `source` = llm | cache (share of answers the cache saved).
    pub const ANSWERS_SERVED: &str = "answers_total";
    /// Router outcome per chat request: `intent`, `tier` (Insights router split).
    pub const ROUTE_DECISIONS: &str = "route_decisions_total";
    /// Jev gate relevance score of every candidate passage (distribution, 0.1 buckets).
    pub const GATE_RELEVANCE: &str = "gate_relevance";
    /// Chat requests that entered planning (the top of the Insights funnel).
    pub const CHAT_REQUESTS: &str = "chat_requests_total";
    /// Requests the injection classifier could not score (`reason` = timeout | error | not_loaded):
    /// guard layer 4 was skipped for them (degraded, never cached).
    pub const CLASSIFIER_SKIPPED: &str = "classifier_skipped_total";
    pub const ACTIONS: &str = "actions_total";
    pub const RATELIMIT_REJECTIONS: &str = "ratelimit_rejections_total";
    pub const DEPENDENCY_UP: &str = "dependency_up";
}

/// Keeps the tracer provider alive; flushes spans on drop (graceful shutdown).
pub struct TelemetryGuard {
    provider: Option<SdkTracerProvider>,
}

impl Drop for TelemetryGuard {
    fn drop(&mut self) {
        if let Some(provider) = self.provider.take()
            && let Err(e) = provider.shutdown()
        {
            eprintln!("telemetry shutdown: {e}");
        }
    }
}

/// Initialise logging + tracing. `otlp_endpoint = None` keeps everything local (tests, offline dev).
pub fn init(service: &'static str, otlp_endpoint: Option<&str>) -> anyhow::Result<TelemetryGuard> {
    opentelemetry::global::set_text_map_propagator(TraceContextPropagator::new());

    let filter = EnvFilter::try_from_default_env().unwrap_or_else(|_| EnvFilter::new("info"));
    let fmt = tracing_subscriber::fmt::layer()
        .json()
        .with_current_span(true)
        .with_target(true)
        .with_filter(filter);

    let provider = match otlp_endpoint {
        Some(endpoint) => {
            let exporter = opentelemetry_otlp::SpanExporter::builder()
                .with_tonic()
                .with_endpoint(endpoint)
                .build()
                .context("building OTLP span exporter")?;
            Some(
                SdkTracerProvider::builder()
                    .with_batch_exporter(exporter)
                    .with_resource(Resource::builder().with_service_name(service).build())
                    .build(),
            )
        }
        None => None,
    };

    let otel = provider
        .as_ref()
        .map(|p| tracing_opentelemetry::layer().with_tracer(p.tracer(service)));

    tracing_subscriber::registry()
        .with(fmt)
        .with(otel)
        .try_init()
        .context("installing tracing subscriber")?;
    if let Some(p) = &provider {
        opentelemetry::global::set_tracer_provider(p.clone());
    }
    Ok(TelemetryGuard { provider })
}

/// Register the known label sets at zero. Prometheus' `increase()` can't see a counter's first
/// increment if the series only appears at that moment, so a quiet gateway's first block, abstention
/// or request would otherwise be missing from every Insights total.
pub fn prime_metrics() {
    use metrics::counter;
    counter!(names::CHAT_REQUESTS).increment(0);
    counter!(names::RATELIMIT_REJECTIONS).increment(0);
    for reason in ["timeout", "error", "not_loaded"] {
        counter!(names::CLASSIFIER_SKIPPED, "reason" => reason).increment(0);
    }
    for source in ["llm", "cache"] {
        counter!(names::ANSWERS_SERVED, "source" => source).increment(0);
    }
    for layer in [
        "regex",
        "jev",
        "classifier",
        "ensemble",
        "documents",
        "output",
        "action_integrity",
    ] {
        counter!(names::GUARD_BLOCKS, "layer" => layer).increment(0);
    }
    for reason in [
        "NoMatch",
        "OutOfScope",
        "ModelAbstained",
        "BlockedInput",
        "BlockedDocuments",
        "BlockedOutput",
        "Locked",
    ] {
        counter!(names::ABSTENTIONS, "reason" => reason).increment(0);
    }
    for layer in ["l1", "l2", "semantic"] {
        for result in ["hit", "miss"] {
            counter!(names::CACHE_REQUESTS, "cache" => "answer", "layer" => layer, "result" => result)
                .increment(0);
        }
    }
}

/// Install the global Prometheus recorder. Latency histograms get explicit buckets so p50/p95/p99
/// can be computed in Prometheus (summaries can't be aggregated across instances).
pub fn init_metrics() -> anyhow::Result<PrometheusHandle> {
    let latency = [
        0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0,
    ];
    PrometheusBuilder::new()
        .set_buckets_for_metric(Matcher::Suffix("_seconds".into()), &latency)?
        .set_buckets_for_metric(
            Matcher::Full(names::GATE_KEPT_CHUNKS.into()),
            &[0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
        )?
        .set_buckets_for_metric(
            Matcher::Full(names::GATE_RELEVANCE.into()),
            &[0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0],
        )?
        .install_recorder()
        .context("installing Prometheus recorder")
}
