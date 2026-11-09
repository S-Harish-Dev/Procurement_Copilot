//! Valkey access with graceful degradation ("Valkey down -> bypass L2 + semantic cache;
//! rate limiting falls back to in-process buckets").
//!
//! The connection is optional: a background task (re)connects every few seconds, and every
//! operation returns `None` when Valkey is unavailable so callers take their fallback path.

use std::sync::Arc;
use std::time::{Duration, Instant};

use dashmap::DashMap;
use metrics::gauge;
use redis::aio::ConnectionManager;
use redis::{AsyncCommands, Script};
use telemetry::names;
use tokio::sync::RwLock;

/// Atomic token bucket: refill by elapsed time, take one token if available.
/// KEYS[1] = bucket key; ARGV = capacity, refill per ms, now (ms). Returns 1 if allowed.
const TOKEN_BUCKET: &str = r"
local cap = tonumber(ARGV[1]); local rate = tonumber(ARGV[2]); local now = tonumber(ARGV[3])
local b = redis.call('HMGET', KEYS[1], 'tokens', 'ts')
local tokens = tonumber(b[1]) or cap
local ts = tonumber(b[2]) or now
tokens = math.min(cap, tokens + (now - ts) * rate)
local allowed = 0
if tokens >= 1 then tokens = tokens - 1; allowed = 1 end
redis.call('HSET', KEYS[1], 'tokens', tokens, 'ts', now)
redis.call('PEXPIRE', KEYS[1], math.ceil(cap / rate) + 1000)
return allowed
";

#[derive(Clone)]
pub struct Valkey {
    conn: Arc<RwLock<Option<ConnectionManager>>>,
    local_buckets: Arc<DashMap<String, (f64, Instant)>>,
    bucket: Script,
}

impl Valkey {
    /// Starts disconnected; `spawn_connector` brings the connection up (and back) in the background.
    pub fn new() -> Self {
        Self {
            conn: Arc::new(RwLock::new(None)),
            local_buckets: Arc::new(DashMap::new()),
            bucket: Script::new(TOKEN_BUCKET),
        }
    }

    pub fn spawn_connector(&self, url: String) {
        let conn = self.conn.clone();
        tokio::spawn(async move {
            loop {
                let healthy = match conn.read().await.clone() {
                    Some(mut c) => redis::cmd("PING")
                        .query_async::<String>(&mut c)
                        .await
                        .is_ok(),
                    None => false,
                };
                if !healthy {
                    let fresh = match redis::Client::open(url.as_str()) {
                        Ok(client) => tokio::time::timeout(
                            Duration::from_secs(2),
                            client.get_connection_manager(),
                        )
                        .await
                        .ok()
                        .and_then(Result::ok),
                        Err(_) => None,
                    };
                    if fresh.is_some() {
                        tracing::info!("valkey connected");
                    }
                    *conn.write().await = fresh;
                }
                gauge!(names::DEPENDENCY_UP, "dependency" => "valkey").set(
                    if conn.read().await.is_some() {
                        1.0
                    } else {
                        0.0
                    },
                );
                tokio::time::sleep(Duration::from_secs(5)).await;
            }
        });
    }

    async fn get(&self) -> Option<ConnectionManager> {
        self.conn.read().await.clone()
    }

    pub async fn is_up(&self) -> bool {
        self.get().await.is_some()
    }

    async fn mark_down(&self) {
        *self.conn.write().await = None;
    }

    /// Per-user token bucket. Valkey first (shared across gateway replicas), in-process fallback.
    pub async fn rate_limit(&self, key: &str, capacity: f64, per_sec: f64) -> bool {
        if let Some(mut c) = self.get().await {
            let now_ms = time::OffsetDateTime::now_utc().unix_timestamp_nanos() / 1_000_000;
            let r: Result<i64, _> = self
                .bucket
                .key(format!("rl:{key}"))
                .arg(capacity)
                .arg(per_sec / 1000.0)
                .arg(now_ms as i64)
                .invoke_async(&mut c)
                .await;
            match r {
                Ok(allowed) => return allowed == 1,
                Err(e) => {
                    tracing::warn!(error = %e, "valkey rate limit failed; using in-process bucket");
                    self.mark_down().await;
                }
            }
        }
        let mut entry = self
            .local_buckets
            .entry(key.to_owned())
            .or_insert((capacity, Instant::now()));
        let (tokens, last) = *entry;
        let refilled = (tokens + last.elapsed().as_secs_f64() * per_sec).min(capacity);
        if refilled >= 1.0 {
            *entry = (refilled - 1.0, Instant::now());
            true
        } else {
            *entry = (refilled, Instant::now());
            false
        }
    }

    /// INCR with expiry; returns the new value (None if Valkey is down).
    pub async fn incr_with_ttl(&self, key: &str, ttl_secs: i64) -> Option<i64> {
        let mut c = self.get().await?;
        let n: i64 = c.incr(key, 1).await.ok()?;
        if n == 1 {
            let _: Result<bool, _> = c.expire(key, ttl_secs).await;
        }
        Some(n)
    }

    pub async fn del(&self, key: &str) -> Option<bool> {
        let mut c = self.get().await?;
        c.del::<_, i64>(key).await.ok().map(|n| n > 0)
    }

    pub async fn get_i64(&self, key: &str) -> Option<i64> {
        let mut c = self.get().await?;
        c.get::<_, Option<i64>>(key).await.ok().flatten()
    }

    #[expect(dead_code, reason = "used by the M7 cache layers")]
    pub async fn incr_by(&self, key: &str, by: i64) -> Option<i64> {
        let mut c = self.get().await?;
        c.incr(key, by).await.ok()
    }

    pub async fn set_ex(&self, key: &str, value: &str, ttl_secs: u64) -> bool {
        let Some(mut c) = self.get().await else {
            return false;
        };
        c.set_ex::<_, _, ()>(key, value, ttl_secs).await.is_ok()
    }

    pub async fn get_string(&self, key: &str) -> Option<String> {
        let mut c = self.get().await?;
        c.get::<_, Option<String>>(key).await.ok().flatten()
    }
}

/// Valkey is L2 for every exact-key cache; any error reads as a miss.
impl cache::L2Store for Valkey {
    async fn get(&self, key: &str) -> Option<String> {
        self.get_string(key).await
    }
    async fn set_ex(&self, key: &str, value: &str, ttl_secs: u64) -> bool {
        Valkey::set_ex(self, key, value, ttl_secs).await
    }
}

impl Valkey {
    /// Delete every key matching `pattern` (SCAN + UNLINK, never KEYS: it would block Valkey).
    pub async fn delete_matching(&self, pattern: &str) -> Option<u64> {
        let mut c = self.get().await?;
        let mut cursor: u64 = 0;
        let mut removed = 0_u64;
        loop {
            let (next, keys): (u64, Vec<String>) = redis::cmd("SCAN")
                .arg(cursor)
                .arg("MATCH")
                .arg(pattern)
                .arg("COUNT")
                .arg(1000)
                .query_async(&mut c)
                .await
                .ok()?;
            if !keys.is_empty() {
                removed += redis::cmd("UNLINK")
                    .arg(&keys)
                    .query_async::<u64>(&mut c)
                    .await
                    .ok()?;
            }
            if next == 0 {
                return Some(removed);
            }
            cursor = next;
        }
    }
}

impl Default for Valkey {
    fn default() -> Self {
        Self::new()
    }
}
