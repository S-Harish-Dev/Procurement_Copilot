//! The gateway's caches, one place so invalidation can't miss one.
//!
//! | cache      | key                                                     | TTL  |
//! |------------|---------------------------------------------------------|------|
//! | answers    | normalized masked query, scope, index, prompt, tier     | 24 h |
//! | semantic   | same filters, cosine ≥ threshold, identical key terms   | 24 h |
//! | retrieval  | exact masked query, scope, index, hybrid params         | 1 h  |
//! | embeddings | model, exact masked query                               | 7 d  |
//! | jev_route  | masked query + recent turns, router question version    | 24 h |
//! | jev_gate   | masked query, chunk id, index, gate question version    | 24 h |
//!
//! Index-version and prompt-version changes invalidate by changing every key; an index bump also
//! clears this replica's L1 and deletes stale semantic entries.

use std::time::Duration;

use cache::{SemanticCache, Tiered};
use jev::RouteDecision;
use retrieval::Hit;
use serde::{Deserialize, Serialize};

use crate::routes::chat::Source;
use crate::valkey::Valkey;

const HOUR: Duration = Duration::from_secs(3600);

/// What an answered request leaves behind. The text is stored *masked* (before any per-user PII
/// unmasking), so serving it re-applies the current requester's own unmasking rules.
#[derive(Clone, Serialize, Deserialize)]
pub struct CachedAnswer {
    pub text: String,
    pub sources: Vec<Source>,
    pub provider: String,
}

pub struct Caches {
    pub answers: Tiered<CachedAnswer, Valkey>,
    pub semantic: Option<SemanticCache>,
    pub retrieval: Tiered<Vec<Hit>, Valkey>,
    pub embeddings: Tiered<Vec<f32>, Valkey>,
    pub jev_route: Tiered<RouteDecision, Valkey>,
    /// `(relevance, injection)` for one passage.
    pub jev_gate: Tiered<(f64, f64), Valkey>,
}

impl Caches {
    pub fn new(valkey: &Valkey, semantic: Option<SemanticCache>) -> Self {
        let l2 = Some(valkey.clone());
        Self {
            answers: Tiered::new("answer", 10_000, 24 * HOUR, l2.clone()),
            semantic,
            retrieval: Tiered::new("retrieval", 5_000, HOUR, l2.clone()),
            embeddings: Tiered::new("embedding", 20_000, 7 * 24 * HOUR, l2.clone()),
            jev_route: Tiered::new("jev_route", 20_000, 24 * HOUR, l2.clone()),
            jev_gate: Tiered::new("jev_gate", 100_000, 24 * HOUR, l2),
        }
    }

    pub fn clear_local(&self) {
        self.answers.clear_local();
        self.retrieval.clear_local();
        self.embeddings.clear_local();
        self.jev_route.clear_local();
        self.jev_gate.clear_local();
    }

    pub fn local_entries(&self) -> Vec<(&'static str, u64)> {
        [
            (self.answers.kind(), self.answers.local_entries()),
            (self.retrieval.kind(), self.retrieval.local_entries()),
            (self.embeddings.kind(), self.embeddings.local_entries()),
            (self.jev_route.kind(), self.jev_route.local_entries()),
            (self.jev_gate.kind(), self.jev_gate.local_entries()),
        ]
        .into()
    }
}

/// Valkey key prefixes owned by the caches (for the admin flush).
pub const L2_PREFIXES: [&str; 5] = ["ans:*", "ret:*", "emb:*", "jevr:*", "jevg:*"];
