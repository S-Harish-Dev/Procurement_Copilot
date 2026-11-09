//! Hybrid retrieval: dense top-40 + BM25 top-40 in one Qdrant batch call, the
//! **same ACL filter on both branches**, fused with RRF here, top-20 returned. Filtering inside the
//! search (not after it) means a restricted chunk can never take a result slot, and a user can't
//! learn that it exists.
//!
//! Fusion is done in the gateway rather than with Qdrant's server-side `Fusion::Rrf` because the
//! server breaks score ties in arbitrary order: dense rank *i* and BM25 rank *i* always tie, and the
//! same query returned a different top-20 order on 24 of 30 runs, moving eval MRR by ±0.02. A
//! ranking that changes between identical calls makes evals and the semantic cache unreliable.

use std::collections::HashMap;

use policy::Scope;
use qdrant_client::Qdrant;
use qdrant_client::qdrant::value::Kind;
use qdrant_client::qdrant::{
    Condition, Filter, QueryBatchPointsBuilder, QueryPointsBuilder, ScoredPoint, Value, VectorInput,
};

pub const COLLECTION: &str = "chunks";

/// Qdrant's own RRF default, kept so fused rankings stay comparable with the recorded baselines.
pub const RRF_K: f32 = 2.0;

#[derive(Debug, thiserror::Error)]
pub enum SearchError {
    #[error("qdrant: {0}")]
    Qdrant(String),
}

#[derive(Clone, Debug, serde::Serialize, serde::Deserialize)]
pub struct Hit {
    pub chunk_id: String,
    pub doc_id: String,
    pub source: String,
    pub title: String,
    pub section_path: String,
    pub classification: String,
    pub department: String,
    /// PII-masked chunk text.
    pub text: String,
    pub injection_flag: bool,
    /// RRF score (rank-based; only meaningful for ordering).
    pub score: f32,
}

#[derive(Clone, Copy, Debug)]
pub struct HybridParams {
    pub dense_k: u64,
    pub sparse_k: u64,
    pub top_k: u64,
}

impl Default for HybridParams {
    fn default() -> Self {
        Self {
            dense_k: 40,
            sparse_k: 40,
            top_k: 20,
        }
    }
}

#[derive(Clone)]
pub struct Retriever {
    client: std::sync::Arc<Qdrant>,
    params: HybridParams,
}

/// One `should` clause per classification grant; plus the active index version as a `must`.
pub fn acl_filter(scope: &Scope, index_version: i64) -> Filter {
    let should: Vec<Condition> = scope
        .grants
        .iter()
        .map(|(classification, depts)| {
            let mut must = vec![Condition::matches("classification", classification.clone())];
            if !depts.iter().any(|d| d == "*") {
                must.push(Condition::matches("department", depts.clone()));
            }
            Condition::from(Filter::must(must))
        })
        .collect();
    let mut f = Filter::should(should);
    f.must = vec![Condition::matches("index_version", index_version)];
    f
}

fn s(payload: &HashMap<String, Value>, key: &str) -> String {
    match payload.get(key).and_then(|v| v.kind.as_ref()) {
        Some(Kind::StringValue(s)) => s.clone(),
        Some(Kind::IntegerValue(i)) => i.to_string(),
        _ => String::new(),
    }
}

fn b(payload: &HashMap<String, Value>, key: &str) -> bool {
    matches!(
        payload.get(key).and_then(|v| v.kind.as_ref()),
        Some(Kind::BoolValue(true))
    )
}

fn to_hit(p: ScoredPoint) -> Hit {
    Hit {
        chunk_id: s(&p.payload, "chunk_id"),
        doc_id: s(&p.payload, "doc_id"),
        source: s(&p.payload, "source"),
        title: s(&p.payload, "title"),
        section_path: s(&p.payload, "section_path"),
        classification: s(&p.payload, "classification"),
        department: s(&p.payload, "department"),
        text: s(&p.payload, "text"),
        injection_flag: b(&p.payload, "injection_flag"),
        score: p.score,
    }
}

/// Reciprocal rank fusion, `score = sum 1 / (k + rank)` with 1-based ranks. Ties are broken by the
/// best single-branch rank, then by chunk id, so identical inputs always give identical output.
pub fn rrf(branches: Vec<Vec<Hit>>, top_k: usize) -> Vec<Hit> {
    let mut fused: HashMap<String, (f32, usize, Hit)> = HashMap::new();
    for branch in branches {
        for (i, hit) in branch.into_iter().enumerate() {
            let contrib = 1.0 / (RRF_K + (i + 1) as f32);
            fused
                .entry(hit.chunk_id.clone())
                .and_modify(|(score, best, _)| {
                    *score += contrib;
                    *best = (*best).min(i);
                })
                .or_insert((contrib, i, hit));
        }
    }
    let mut out: Vec<(f32, usize, Hit)> = fused.into_values().collect();
    out.sort_by(|a, b| {
        b.0.total_cmp(&a.0)
            .then(a.1.cmp(&b.1))
            .then_with(|| a.2.chunk_id.cmp(&b.2.chunk_id))
    });
    out.into_iter()
        .take(top_k)
        .map(|(score, _, mut hit)| {
            hit.score = score;
            hit
        })
        .collect()
}

impl Retriever {
    pub fn connect(url: &str) -> Result<Self, SearchError> {
        let client = Qdrant::from_url(url)
            .build()
            .map_err(|e| SearchError::Qdrant(e.to_string()))?;
        Ok(Self {
            client: std::sync::Arc::new(client),
            params: HybridParams::default(),
        })
    }

    pub fn params(&self) -> HybridParams {
        self.params
    }

    pub fn with_params(mut self, params: HybridParams) -> Self {
        self.params = params;
        self
    }

    pub async fn hybrid(
        &self,
        dense: Vec<f32>,
        sparse_indices: Vec<u32>,
        scope: &Scope,
        index_version: i64,
    ) -> Result<Vec<Hit>, SearchError> {
        let filter = acl_filter(scope, index_version);
        if dense.is_empty() && sparse_indices.is_empty() {
            return Ok(Vec::new());
        }
        // Either branch may be empty (ablations: dense-only / BM25-only); fusion over one branch is
        // just that branch's ranking.
        let mut branches = Vec::with_capacity(2);
        if !dense.is_empty() {
            branches.push(
                QueryPointsBuilder::new(COLLECTION)
                    .query(dense)
                    .using("dense")
                    .filter(filter.clone())
                    .limit(self.params.dense_k)
                    .with_payload(true)
                    .build(),
            );
        }
        // A query made only of stopwords has no BM25 terms; skip that branch instead of sending
        // an empty sparse vector.
        if !sparse_indices.is_empty() {
            let values = vec![1.0_f32; sparse_indices.len()];
            branches.push(
                QueryPointsBuilder::new(COLLECTION)
                    .query(VectorInput::new_sparse(sparse_indices, values))
                    .using("bm25")
                    .filter(filter)
                    .limit(self.params.sparse_k)
                    .with_payload(true)
                    .build(),
            );
        }
        let resp = self
            .client
            .query_batch(QueryBatchPointsBuilder::new(COLLECTION, branches))
            .await
            .map_err(|e| SearchError::Qdrant(e.to_string()))?;
        let ranked: Vec<Vec<Hit>> = resp
            .result
            .into_iter()
            .map(|branch| branch.result.into_iter().map(to_hit).collect())
            .collect();
        Ok(rrf(ranked, self.params.top_k as usize))
    }

    pub async fn health(&self) -> bool {
        self.client.health_check().await.is_ok()
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::BTreeMap;

    fn hit(id: &str) -> Hit {
        Hit {
            chunk_id: id.to_owned(),
            doc_id: String::new(),
            source: String::new(),
            title: String::new(),
            section_path: String::new(),
            classification: String::new(),
            department: String::new(),
            text: String::new(),
            injection_flag: false,
            score: 0.0,
        }
    }

    fn ids(hits: &[Hit]) -> Vec<&str> {
        hits.iter().map(|h| h.chunk_id.as_str()).collect()
    }

    #[test]
    fn rrf_rewards_agreement_and_breaks_ties_deterministically() {
        let dense = vec![hit("a"), hit("b"), hit("c")];
        let sparse = vec![hit("z"), hit("b"), hit("y")];
        let fused = rrf(vec![dense.clone(), sparse.clone()], 10);
        // b is in both branches; a and z tie at rank 1 -> chunk id order.
        assert_eq!(ids(&fused), ["b", "a", "z", "c", "y"]);
        for _ in 0..20 {
            assert_eq!(
                ids(&rrf(vec![sparse.clone(), dense.clone()], 10)),
                ids(&fused)
            );
        }
        assert_eq!(ids(&rrf(vec![dense], 2)), ["a", "b"]);
    }

    #[test]
    fn acl_filter_has_one_should_clause_per_grant_and_version_must() {
        let scope = Scope::from_grants(
            "employee",
            BTreeMap::from([
                ("public".to_owned(), vec!["*".to_owned()]),
                (
                    "internal".to_owned(),
                    vec!["IT".to_owned(), "all".to_owned()],
                ),
            ]),
            false,
        );
        let f = acl_filter(&scope, 3);
        assert_eq!(f.should.len(), 2);
        assert_eq!(f.must.len(), 1);
        let dbg = format!("{f:?}");
        assert!(dbg.contains("internal") && dbg.contains("IT") && !dbg.contains("confidential"));
    }
}
