//! Request/response guardrails that run in-process on the hot path.
//!
//! Layers here are deterministic and cheap (microseconds). The model-based layers — Jev's
//! `prompt_injection` question and Prompt Guard 2 — are combined with these in the api crate.

pub mod injection;
pub mod normalize;
pub mod output;
pub mod pii;

pub use injection::{InjectionScanner, ScanResult};
pub use normalize::normalize;
pub use output::{
    ABSTAIN_TOKEN, Block, OutputPolicy, OutputReport, Segmenter, is_abstention, scan_segment,
};
pub use pii::{PiiMap, mask_pii};
