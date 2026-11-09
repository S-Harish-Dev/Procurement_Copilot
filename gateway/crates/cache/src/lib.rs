//! Response caching: scope-keyed exact caches (moka L1 → Valkey L2) and a
//! semantic answer cache in Qdrant.
//!
//! The rule every key follows: anything that can change *what a user may see* (permission scope,
//! active index version, prompt version, model tier) is part of the key or the filter, so a cached
//! answer can only be served to a request that would have produced it. Blocked requests, actions
//! and abstentions are never written.

pub mod keys;
pub mod semantic;
pub mod tiered;

pub use keys::{
    answer_key, embedding_key, gate_key, key_terms, normalize_query, retrieval_key, route_key,
};
pub use semantic::{SemanticCache, SemanticScope};
pub use tiered::{L2Store, Layer, Tiered};
