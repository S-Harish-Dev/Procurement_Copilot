//! Semantic answer cache (`semcache`): a paraphrase of an answered question gets the stored
//! answer, but only inside the exact same scope, index version, prompt version and model tier
//! (all hard filters, never part of the similarity), above a strict cosine threshold, and only if
//! the numbers/identifiers in both questions are identical (`key_terms`).
//!
//! Every failure is a miss: the cache can make a request faster, never fail it.

use std::collections::HashMap;
use std::sync::Arc;
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use qdrant_client::qdrant::value::Kind;
use qdrant_client::qdrant::{
    Condition, CreateCollectionBuilder, CreateFieldIndexCollectionBuilder, DeletePointsBuilder,
    Distance, FieldType, Filter, PointStruct, QueryPointsBuilder, Range, UpsertPointsBuilder,
    Value, VectorParamsBuilder,
};
use qdrant_client::{Payload, Qdrant};

use crate::tiered::{Layer, record};

pub const COLLECTION: &str = "semcache";

#[derive(Debug, thiserror::Error)]
#[error("semantic cache: {0}")]
pub struct SemanticError(String);

/// Everything that must match exactly for a cached answer to be eligible.
pub struct SemanticScope<'a> {
    pub scope_hash: &'a str,
    pub index_version: i64,
    pub prompt_version: &'a str,
    pub tier: &'a str,
    pub key_terms: &'a str,
}

impl SemanticScope<'_> {
    fn filter(&self, not_before: i64) -> Filter {
        Filter::must([
            Condition::matches("scope_hash", self.scope_hash.to_owned()),
            Condition::matches("index_version", self.index_version),
            Condition::matches("prompt_version", self.prompt_version.to_owned()),
            Condition::matches("model_tier", self.tier.to_owned()),
            Condition::matches("key_terms", self.key_terms.to_owned()),
            Condition::range(
                "created_at",
                Range {
                    gte: Some(not_before as f64),
                    ..Range::default()
                },
            ),
        ])
    }
}

#[derive(Clone)]
pub struct SemanticCache {
    client: Arc<Qdrant>,
    pub threshold: f32,
    ttl: Duration,
    dim: u64,
}

fn now() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

fn err(e: impl std::fmt::Display) -> SemanticError {
    SemanticError(e.to_string())
}

impl SemanticCache {
    pub fn connect(
        url: &str,
        threshold: f32,
        ttl: Duration,
        dim: u64,
    ) -> Result<Self, SemanticError> {
        let client = Qdrant::from_url(url).build().map_err(err)?;
        Ok(Self {
            client: Arc::new(client),
            threshold,
            ttl,
            dim,
        })
    }

    /// Create the collection and its payload indexes if missing (idempotent).
    pub async fn ensure_collection(&self) -> Result<(), SemanticError> {
        if self
            .client
            .collection_exists(COLLECTION)
            .await
            .map_err(err)?
        {
            return Ok(());
        }
        self.client
            .create_collection(
                CreateCollectionBuilder::new(COLLECTION)
                    .vectors_config(VectorParamsBuilder::new(self.dim, Distance::Cosine)),
            )
            .await
            .map_err(err)?;
        for (field, kind) in [
            ("scope_hash", FieldType::Keyword),
            ("prompt_version", FieldType::Keyword),
            ("model_tier", FieldType::Keyword),
            ("key_terms", FieldType::Keyword),
            ("index_version", FieldType::Integer),
            ("created_at", FieldType::Integer),
        ] {
            self.client
                .create_field_index(CreateFieldIndexCollectionBuilder::new(
                    COLLECTION, field, kind,
                ))
                .await
                .map_err(err)?;
        }
        Ok(())
    }

    /// The stored value (JSON) and its cosine similarity, if a close-enough paraphrase exists.
    pub async fn lookup(&self, vector: &[f32], scope: &SemanticScope<'_>) -> Option<(String, f32)> {
        let not_before = now() - self.ttl.as_secs() as i64;
        let res = self
            .client
            .query(
                QueryPointsBuilder::new(COLLECTION)
                    .query(vector.to_vec())
                    .filter(scope.filter(not_before))
                    .score_threshold(self.threshold)
                    .limit(1)
                    .with_payload(true),
            )
            .await;
        let found = match res {
            Ok(r) => r.result.into_iter().next().and_then(|p| {
                match p.payload.get("value").and_then(|v| v.kind.as_ref()) {
                    Some(Kind::StringValue(s)) => Some((s.clone(), p.score)),
                    _ => None,
                }
            }),
            Err(e) => {
                tracing::debug!(error = %e, "semantic cache lookup failed; miss");
                None
            }
        };
        record("answer", Layer::Semantic, found.is_some());
        found
    }

    /// Store an answer. The point id is derived from the exact answer key, so re-answering the
    /// same question overwrites instead of piling up near-duplicates.
    pub async fn store(
        &self,
        answer_key: &str,
        vector: Vec<f32>,
        scope: &SemanticScope<'_>,
        value: String,
    ) {
        let id = uuid::Uuid::new_v5(&uuid::Uuid::NAMESPACE_OID, answer_key.as_bytes()).to_string();
        let payload: Payload = HashMap::<&str, Value>::from([
            ("scope_hash", scope.scope_hash.into()),
            ("index_version", scope.index_version.into()),
            ("prompt_version", scope.prompt_version.into()),
            ("model_tier", scope.tier.into()),
            ("key_terms", scope.key_terms.into()),
            ("created_at", now().into()),
            ("value", value.into()),
        ])
        .into();
        let r = self
            .client
            .upsert_points(UpsertPointsBuilder::new(
                COLLECTION,
                vec![PointStruct::new(id, vector, payload)],
            ))
            .await;
        if let Err(e) = r {
            tracing::debug!(error = %e, "semantic cache store failed");
        }
    }

    /// After an index bump, entries for other versions can never match again: delete them.
    pub async fn purge_other_versions(&self, keep: i64) -> Result<(), SemanticError> {
        self.client
            .delete_points(
                DeletePointsBuilder::new(COLLECTION).points(Filter::must_not([
                    Condition::matches("index_version", keep),
                ])),
            )
            .await
            .map(|_| ())
            .map_err(err)
    }

    /// Admin flush: drop every entry.
    pub async fn clear(&self) -> Result<(), SemanticError> {
        self.client
            .delete_points(DeletePointsBuilder::new(COLLECTION).points(Filter::default()))
            .await
            .map(|_| ())
            .map_err(err)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn filter_pins_scope_version_prompt_tier_terms_and_age() {
        let s = SemanticScope {
            scope_hash: "abc",
            index_version: 7,
            prompt_version: "v1",
            tier: "cheap",
            key_terms: "25000",
        };
        let f = s.filter(100);
        assert_eq!(f.must.len(), 6);
        let dbg = format!("{f:?}");
        for needle in [
            "scope_hash",
            "abc",
            "index_version",
            "prompt_version",
            "model_tier",
            "key_terms",
            "created_at",
        ] {
            assert!(dbg.contains(needle), "{needle} missing from {dbg}");
        }
    }
}
