//! Exact-key cache in two tiers: moka in process (L1), Valkey shared across gateway replicas (L2).
//!
//! L2 is optional and may be down: every L2 error is a miss, never
//! a failed request. Values are JSON in L2 so another replica (or an operator with `valkey-cli`)
//! can read them.

use std::future::Future;
use std::sync::Arc;
use std::time::Duration;

use metrics::counter;
use moka::future::Cache;
use serde::Serialize;
use serde::de::DeserializeOwned;
use telemetry::names;

/// The shared store behind L1. Implemented by the gateway's Valkey client.
pub trait L2Store: Clone + Send + Sync + 'static {
    fn get(&self, key: &str) -> impl Future<Output = Option<String>> + Send;
    fn set_ex(&self, key: &str, value: &str, ttl_secs: u64) -> impl Future<Output = bool> + Send;
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum Layer {
    L1,
    L2,
    Semantic,
}

impl Layer {
    pub fn as_str(self) -> &'static str {
        match self {
            Layer::L1 => "l1",
            Layer::L2 => "l2",
            Layer::Semantic => "semantic",
        }
    }
}

/// L1 entries live at most this long even when the L2 TTL is longer, which bounds how stale one
/// replica can be after another replica's invalidation.
const L1_MAX_TTL: Duration = Duration::from_secs(3600);

pub fn record(kind: &'static str, layer: Layer, hit: bool) {
    counter!(names::CACHE_REQUESTS, "cache" => kind, "layer" => layer.as_str(),
             "result" => if hit { "hit" } else { "miss" })
    .increment(1);
}

#[derive(Clone)]
pub struct Tiered<V, S> {
    kind: &'static str,
    l1: Cache<String, Arc<V>>,
    l2: Option<S>,
    ttl: Duration,
}

impl<V, S> Tiered<V, S>
where
    V: Serialize + DeserializeOwned + Send + Sync + 'static,
    S: L2Store,
{
    /// `kind` labels the metrics (`answer`, `embedding`, `retrieval`, `jev_route`, `jev_gate`).
    pub fn new(kind: &'static str, capacity: u64, ttl: Duration, l2: Option<S>) -> Self {
        Self {
            kind,
            l1: Cache::builder()
                .max_capacity(capacity)
                .time_to_live(ttl.min(L1_MAX_TTL))
                .build(),
            l2,
            ttl,
        }
    }

    pub async fn get(&self, key: &str) -> Option<(Arc<V>, Layer)> {
        if let Some(v) = self.l1.get(key).await {
            record(self.kind, Layer::L1, true);
            return Some((v, Layer::L1));
        }
        record(self.kind, Layer::L1, false);
        let l2 = self.l2.as_ref()?;
        let found = l2
            .get(key)
            .await
            .and_then(|raw| serde_json::from_str::<V>(&raw).ok())
            .map(Arc::new);
        record(self.kind, Layer::L2, found.is_some());
        let v = found?;
        self.l1.insert(key.to_owned(), v.clone()).await;
        Some((v, Layer::L2))
    }

    pub async fn put(&self, key: &str, value: V) {
        if let Some(l2) = &self.l2
            && let Ok(raw) = serde_json::to_string(&value)
        {
            l2.set_ex(key, &raw, self.ttl.as_secs()).await;
        }
        self.l1.insert(key.to_owned(), Arc::new(value)).await;
    }

    /// Drop this replica's L1 (L2 entries are invalidated by their keys changing).
    pub fn clear_local(&self) {
        self.l1.invalidate_all();
    }

    pub fn kind(&self) -> &'static str {
        self.kind
    }

    pub fn local_entries(&self) -> u64 {
        self.l1.entry_count()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashMap;
    use std::sync::Mutex;

    #[derive(Clone, Default)]
    struct MemL2 {
        map: Arc<Mutex<HashMap<String, String>>>,
        down: bool,
    }

    impl L2Store for MemL2 {
        async fn get(&self, key: &str) -> Option<String> {
            if self.down {
                return None;
            }
            self.map.lock().unwrap().get(key).cloned()
        }
        async fn set_ex(&self, key: &str, value: &str, _ttl: u64) -> bool {
            if self.down {
                return false;
            }
            self.map.lock().unwrap().insert(key.into(), value.into());
            true
        }
    }

    #[tokio::test]
    async fn l2_is_shared_and_promoted_into_l1() {
        let l2 = MemL2::default();
        let a: Tiered<Vec<u32>, MemL2> =
            Tiered::new("t", 10, Duration::from_secs(60), Some(l2.clone()));
        let b: Tiered<Vec<u32>, MemL2> = Tiered::new("t", 10, Duration::from_secs(60), Some(l2));
        a.put("k", vec![1, 2]).await;
        let (v, layer) = b.get("k").await.unwrap();
        assert_eq!((v.as_slice(), layer), (&[1, 2][..], Layer::L2));
        assert_eq!(b.get("k").await.unwrap().1, Layer::L1);
        assert!(b.get("other").await.is_none());
    }

    #[tokio::test]
    async fn l2_down_degrades_to_l1_only() {
        let l2 = MemL2 {
            down: true,
            ..MemL2::default()
        };
        let c: Tiered<String, MemL2> = Tiered::new("t", 10, Duration::from_secs(60), Some(l2));
        assert!(c.get("k").await.is_none());
        c.put("k", "v".into()).await;
        assert_eq!(c.get("k").await.unwrap().1, Layer::L1);
        c.clear_local();
        assert!(c.get("k").await.is_none());
    }
}
