//! Query embedding with nomic-embed-text-v1.5 (fastembed-rs, local ONNX). Same model as the
//! documents (Python fastembed); the "search_query: " prefix is what nomic v1.5 expects for queries.
//!
//! fastembed's `embed` needs `&mut self`, so a small pool of model instances sits behind mutexes
//! and inference runs on blocking threads — the async runtime never stalls on a forward pass.

use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use fastembed::{EmbeddingModel, TextEmbedding, TextInitOptions};
use tokio::sync::Semaphore;

pub const QUERY_PREFIX: &str = "search_query: ";
/// Model id, part of the embedding-cache key.
pub const MODEL_ID: &str = "nomic-embed-text-v1.5";
/// Output dimension (the semantic cache collection is created with it).
pub const DIM: u64 = 768;

#[derive(Debug, thiserror::Error)]
pub enum EmbedError {
    #[error("embedding model: {0}")]
    Model(String),
    #[error("embedding worker panicked")]
    Join,
}

#[derive(Clone)]
pub struct QueryEmbedder {
    pool: Arc<Vec<Mutex<TextEmbedding>>>,
    permits: Arc<Semaphore>,
}

impl QueryEmbedder {
    /// Loads `instances` copies of the model (each ~0.5 GB RAM). Downloads on first use into
    /// `cache_dir` (FASTEMBED_CACHE_DIR), so containers can mount a persistent model cache.
    /// `env`: the process-wide ONNX Runtime environment (ORT allows only one per process; the
    /// gateway shares it with the injection classifier).
    /// `threads`: intra-op threads per instance. fastembed's default is *every* core per instance,
    /// so N instances oversubscribe the CPU N-fold, and under concurrent load threads mostly wait
    /// on each other. Instances × threads should stay near the cores the gateway may use.
    pub fn load(
        instances: usize,
        threads: usize,
        cache_dir: PathBuf,
        env: &ort::environment::Environment,
    ) -> Result<Self, EmbedError> {
        let instances = instances.max(1);
        let mut pool = Vec::with_capacity(instances);
        for _ in 0..instances {
            let opts = TextInitOptions::new(EmbeddingModel::NomicEmbedTextV15)
                .with_cache_dir(cache_dir.clone())
                .with_show_download_progress(false)
                .with_environment(env.clone())
                .with_intra_threads(threads.max(1));
            pool.push(Mutex::new(
                TextEmbedding::try_new(opts).map_err(|e| EmbedError::Model(e.to_string()))?,
            ));
        }
        Ok(Self {
            pool: Arc::new(pool),
            permits: Arc::new(Semaphore::new(instances)),
        })
    }

    /// L2-normalised query vector (the raw nomic output is not unit length; see Limitation.md).
    pub async fn embed(&self, query: &str) -> Result<Vec<f32>, EmbedError> {
        let _permit = self.permits.acquire().await.map_err(|_| EmbedError::Join)?;
        let pool = self.pool.clone();
        let text = format!("{QUERY_PREFIX}{query}");
        tokio::task::spawn_blocking(move || {
            // A permit guarantees at least one instance is free; take the first unlocked one.
            let mut model = pool
                .iter()
                .find_map(|m| m.try_lock().ok())
                .unwrap_or_else(|| pool[0].lock().expect("embedder mutex poisoned"));
            let mut out = model
                .embed(vec![text], Some(1))
                .map_err(|e| EmbedError::Model(e.to_string()))?;
            let mut v = out
                .pop()
                .ok_or_else(|| EmbedError::Model("empty output".into()))?;
            normalize(&mut v);
            Ok(v)
        })
        .await
        .map_err(|_| EmbedError::Join)?
    }
}

pub fn normalize(v: &mut [f32]) {
    let norm = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm > 0.0 {
        v.iter_mut().for_each(|x| *x /= norm);
    }
}

pub fn cosine(a: &[f32], b: &[f32]) -> f32 {
    let dot: f32 = a.iter().zip(b).map(|(x, y)| x * y).sum();
    let na = a.iter().map(|x| x * x).sum::<f32>().sqrt();
    let nb = b.iter().map(|x| x * x).sum::<f32>().sqrt();
    dot / (na * nb)
}

#[cfg(test)]
mod tests {
    use super::*;

    /// Needs the ~0.5 GB model download, so it is opt-in: `cargo test -p retrieval -- --ignored`.
    #[tokio::test]
    #[ignore = "downloads nomic-embed-text-v1.5; run with --ignored (make parity)"]
    async fn dense_parity_with_python_fastembed() {
        let fixture: Vec<serde_json::Value> =
            serde_json::from_str(include_str!("../testdata/dense_parity.json")).unwrap();
        let cache =
            std::env::var("FASTEMBED_CACHE_DIR").unwrap_or_else(|_| ".fastembed_cache".into());
        let env = ort::init().build().unwrap();
        let e = QueryEmbedder::load(1, 4, cache.into(), &env).unwrap();
        for case in fixture {
            let expected: Vec<f32> = case["vector"]
                .as_array()
                .unwrap()
                .iter()
                .map(|x| x.as_f64().unwrap() as f32)
                .collect();
            let got = e.embed(case["query"].as_str().unwrap()).await.unwrap();
            let c = cosine(&got, &expected);
            assert!(c >= 0.999, "cosine {c} for {}", case["query"]);
        }
    }
}
