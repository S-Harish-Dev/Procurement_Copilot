//! BM25 query vectors, bit-compatible with fastembed's Python `Qdrant/bm25` (used for documents).
//!
//! fastembed-rs has no BM25 model, so this reimplements the Python query path exactly:
//! 1. replace every char that is not `\w` or whitespace with a space; lowercase; split;
//! 2. drop stopwords (the model's `english.txt`, vendored in gateway/assets) and tokens > 40 chars;
//! 3. Snowball English stem (`rust-stemmers` — the crate `py_rust_stemmers` wraps);
//! 4. token id = |murmur3_x86_32(token, seed 0)| as signed i32, weight 1.0 per *unique* token.
//!
//! Document-side term frequencies and IDF are already in Qdrant (modifier = idf), so the query
//! only needs the set of token ids. Parity is tested against fixtures generated in Python.

use std::collections::{BTreeSet, HashSet};
use std::sync::LazyLock;

use regex::Regex;
use rust_stemmers::{Algorithm, Stemmer};

const TOKEN_MAX_CHARS: usize = 40;

static NON_WORD: LazyLock<Regex> = LazyLock::new(|| Regex::new(r"[^\w\s]").expect("re"));

pub struct Bm25 {
    stopwords: HashSet<String>,
    stemmer: Stemmer,
}

impl Bm25 {
    pub fn new(stopwords_file: &str) -> Self {
        Self {
            stopwords: stopwords_file
                .lines()
                .map(|l| l.trim().to_lowercase())
                .filter(|l| !l.is_empty())
                .collect(),
            stemmer: Stemmer::create(Algorithm::English),
        }
    }

    pub fn tokens(&self, text: &str) -> Vec<String> {
        let cleaned = NON_WORD.replace_all(text, " ").to_lowercase();
        cleaned
            .split_whitespace()
            .filter(|t| !self.stopwords.contains(*t) && t.chars().count() <= TOKEN_MAX_CHARS)
            .map(|t| self.stemmer.stem(t).into_owned())
            .filter(|t| !t.is_empty())
            .collect()
    }

    /// Sorted unique token ids; every weight is 1.0.
    pub fn query_indices(&self, text: &str) -> Vec<u32> {
        let ids: BTreeSet<u32> = self.tokens(text).iter().map(|t| token_id(t)).collect();
        ids.into_iter().collect()
    }
}

/// `abs(mmh3.hash(token))` in Python: murmur3 x86 32-bit, seed 0, interpreted as signed i32.
pub fn token_id(token: &str) -> u32 {
    (murmur3_x86_32(token.as_bytes(), 0) as i32).unsigned_abs()
}

/// MurmurHash3 x86_32 (public domain algorithm by Austin Appleby), ~25 lines, no dependency.
pub fn murmur3_x86_32(data: &[u8], seed: u32) -> u32 {
    const C1: u32 = 0xcc9e_2d51;
    const C2: u32 = 0x1b87_3593;
    let mut h = seed;
    let (blocks, tail) = data.as_chunks::<4>();
    for block in blocks {
        let mut k = u32::from_le_bytes(*block);
        k = k.wrapping_mul(C1).rotate_left(15).wrapping_mul(C2);
        h ^= k;
        h = h.rotate_left(13).wrapping_mul(5).wrapping_add(0xe654_6b64);
    }
    let mut k: u32 = 0;
    for (i, b) in tail.iter().enumerate() {
        k ^= u32::from(*b) << (8 * i);
    }
    if !tail.is_empty() {
        k = k.wrapping_mul(C1).rotate_left(15).wrapping_mul(C2);
        h ^= k;
    }
    h ^= data.len() as u32;
    h ^= h >> 16;
    h = h.wrapping_mul(0x85eb_ca6b);
    h ^= h >> 13;
    h = h.wrapping_mul(0xc2b2_ae35);
    h ^ (h >> 16)
}

#[cfg(test)]
mod tests {
    use super::*;

    const STOPWORDS: &str = include_str!("../../../assets/bm25_stopwords_english.txt");
    const FIXTURE: &str = include_str!("../testdata/bm25_parity.json");

    #[test]
    fn murmur3_known_vectors() {
        // Reference values from the canonical C implementation / Python mmh3 (unsigned).
        assert_eq!(murmur3_x86_32(b"", 0), 0);
        assert_eq!(murmur3_x86_32(b"hello", 0), 0x248b_fa47);
        assert_eq!(
            murmur3_x86_32(b"The quick brown fox jumps over the lazy dog", 0),
            0x2e4f_f723
        );
    }

    #[test]
    fn parity_with_python_fastembed() {
        let bm25 = Bm25::new(STOPWORDS);
        let cases: Vec<serde_json::Value> = serde_json::from_str(FIXTURE).unwrap();
        for case in cases {
            let q = case["query"].as_str().unwrap();
            let expected: Vec<u32> = case["indices"]
                .as_array()
                .unwrap()
                .iter()
                .map(|v| v.as_u64().unwrap() as u32)
                .collect();
            assert_eq!(
                bm25.query_indices(q),
                expected,
                "mismatch for {q:?}: tokens {:?}",
                bm25.tokens(q)
            );
        }
    }
}
