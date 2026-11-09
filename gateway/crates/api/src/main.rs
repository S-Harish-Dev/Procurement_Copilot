//! Procurement Copilot gateway: edge, policy, RAG fast path and the single choke point for every
//! outbound LLM / Jev call.

mod brain;
mod caching;
mod config;
mod http;
mod routes;
mod state;
mod traces;
mod valkey;

use anyhow::Context;
use tokio::net::TcpListener;
use tokio_util::sync::CancellationToken;

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    let cfg = config::Config::from_env();
    let _telemetry = telemetry::init("gateway", cfg.otlp_endpoint.as_deref())?;
    let metrics = telemetry::init_metrics()?;
    telemetry::prime_metrics();
    let state = state::AppState::build(cfg.clone(), metrics)
        .await
        .context("building gateway state")?;

    let shutdown = CancellationToken::new();
    let servers = [
        (
            "public",
            cfg.bind_public.clone(),
            routes::public(state.clone()),
        ),
        (
            "internal",
            cfg.bind_internal.clone(),
            routes::internal(state.clone()),
        ),
        (
            "metrics",
            cfg.bind_metrics.clone(),
            routes::metrics(state.clone()),
        ),
    ];
    let mut tasks = Vec::new();
    for (name, addr, app) in servers {
        let listener = TcpListener::bind(&addr)
            .await
            .with_context(|| format!("binding {name} on {addr}"))?;
        tracing::info!(listener = name, %addr, "listening");
        let token = shutdown.clone();
        tasks.push(tokio::spawn(async move {
            axum::serve(listener, app)
                .with_graceful_shutdown(async move { token.cancelled().await })
                .await
        }));
    }

    wait_for_signal().await;
    tracing::info!("shutdown requested: draining in-flight requests");
    shutdown.cancel();
    for t in tasks {
        t.await??;
    }
    Ok(())
}

async fn wait_for_signal() {
    let ctrl_c = tokio::signal::ctrl_c();
    #[cfg(unix)]
    {
        let mut term = tokio::signal::unix::signal(tokio::signal::unix::SignalKind::terminate())
            .expect("SIGTERM handler");
        tokio::select! {
            _ = ctrl_c => {},
            _ = term.recv() => {},
        }
    }
    #[cfg(not(unix))]
    {
        let _ = ctrl_c.await;
    }
}
