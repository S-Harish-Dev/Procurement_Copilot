//! Query-side retrieval: BM25 + nomic query vectors, and the hybrid Qdrant query.

pub mod bm25;
pub mod embed;
pub mod search;

pub use bm25::Bm25;
pub use embed::{EmbedError, QueryEmbedder};
pub use search::{Hit, HybridParams, Retriever, SearchError, acl_filter};
