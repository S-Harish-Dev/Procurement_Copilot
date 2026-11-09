//! `providers.toml`: the ordered fallback chain and each provider's limits and list prices.

use serde::Deserialize;

#[derive(Clone, Debug, Deserialize)]
pub struct LlmConfig {
    /// Retries on the same provider for retryable errors (429, 5xx, timeout) before falling back.
    #[serde(default = "default_retries")]
    pub retries: u32,
    #[serde(default)]
    pub breaker: BreakerConfig,
    /// Fallback order = file order.
    pub providers: Vec<ProviderConfig>,
}

fn default_retries() -> u32 {
    2
}

#[derive(Clone, Debug, Deserialize)]
pub struct BreakerConfig {
    pub failure_threshold: u32,
    pub cooldown_secs: u64,
}

impl Default for BreakerConfig {
    fn default() -> Self {
        Self {
            failure_threshold: 3,
            cooldown_secs: 30,
        }
    }
}

#[derive(Clone, Debug, Deserialize)]
pub struct ProviderConfig {
    pub name: String,
    pub base_url: String,
    /// Env var holding the API key. Missing/empty key = provider skipped (logged once at startup).
    pub api_key_env: String,
    pub models: TierModels,
    #[serde(default)]
    pub limits: Limits,
    /// List prices (USD per 1M tokens) — used for the *shadow* cost: what this traffic would cost
    /// on a paid tier. Actual cost is 0 when `free = true`.
    #[serde(default)]
    pub price_in_per_m: f64,
    #[serde(default)]
    pub price_out_per_m: f64,
    #[serde(default = "default_true")]
    pub free: bool,
    #[serde(default = "default_timeout")]
    pub timeout_ms: u64,
    /// Max wait for the first streamed event; a stalled provider is abandoned before any token
    /// reaches the user, so falling back stays invisible.
    #[serde(default = "default_ttft")]
    pub first_token_timeout_ms: u64,
}

fn default_true() -> bool {
    true
}
fn default_timeout() -> u64 {
    60_000
}
fn default_ttft() -> u64 {
    8_000
}

#[derive(Clone, Debug, Deserialize)]
pub struct TierModels {
    pub cheap: String,
    pub expensive: String,
}

/// Free-tier quotas. 0 = unlimited. Tracked in-process (one gateway instance in this deployment).
#[derive(Clone, Debug, Default, Deserialize)]
pub struct Limits {
    #[serde(default)]
    pub rpm: u32,
    #[serde(default)]
    pub rpd: u32,
    #[serde(default)]
    pub tpm: u32,
}

impl LlmConfig {
    pub fn from_toml(text: &str) -> Result<Self, toml::de::Error> {
        toml::from_str(text)
    }
}
