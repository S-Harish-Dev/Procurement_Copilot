//! Cache-key derivation.
//!
//! Keys are SHA-256 over *length-prefixed* fields: a plain `a|b` join would let `("x|y", "z")` and
//! `("x", "y|z")` collide, and the query is user-controlled text.

use sha2::{Digest, Sha256};

/// Lower-case, collapse whitespace, drop trailing `?`, `.` and `!`: "What is X?" and "what is x"
/// share an answer. Only used for the answer cache; embeddings and retrieval key on the exact
/// masked query so their results stay bit-identical to an uncached run.
pub fn normalize_query(q: &str) -> String {
    let lower = q.to_lowercase();
    let joined = lower.split_whitespace().collect::<Vec<_>>().join(" ");
    joined.trim_end_matches(['?', '.', '!', ' ']).to_owned()
}

fn digest(parts: &[&str]) -> String {
    let mut h = Sha256::new();
    for p in parts {
        h.update((p.len() as u64).to_le_bytes());
        h.update(p.as_bytes());
    }
    h.finalize().iter().map(|b| format!("{b:02x}")).collect()
}

/// Final answers: `sha256(normalized_masked_query | scope_hash | index_version | prompt_version | model_tier)`.
pub fn answer_key(
    masked_query: &str,
    scope_hash: &str,
    index_version: i64,
    prompt_version: &str,
    tier: &str,
) -> String {
    let q = normalize_query(masked_query);
    let v = index_version.to_string();
    format!(
        "ans:{}",
        digest(&[&q, scope_hash, &v, prompt_version, tier])
    )
}

/// Fused retrieval candidates (already ACL-filtered, hence the scope).
pub fn retrieval_key(
    masked_query: &str,
    scope_hash: &str,
    index_version: i64,
    params: &str,
) -> String {
    let v = index_version.to_string();
    format!("ret:{}", digest(&[masked_query, scope_hash, &v, params]))
}

/// Query embeddings: a pure function of model and text, so no scope.
pub fn embedding_key(model: &str, masked_query: &str) -> String {
    format!("emb:{}", digest(&[model, masked_query]))
}

/// Jev router decision for the masked query plus the recent user turns it was given.
pub fn route_key(masked_query: &str, recent: &[String], router_version: &str) -> String {
    let mut parts: Vec<&str> = vec![router_version, masked_query];
    parts.extend(recent.iter().map(String::as_str));
    format!("jevr:{}", digest(&parts))
}

/// Jev gate scores for one passage: `(query_hash, chunk_id, index_version, gate_version)`.
pub fn gate_key(
    masked_query: &str,
    chunk_id: &str,
    index_version: i64,
    gate_version: &str,
) -> String {
    let v = index_version.to_string();
    format!(
        "jevg:{}",
        digest(&[masked_query, chunk_id, &v, gate_version])
    )
}

/// Tokens that carry a number or an identifier ("25k", "C-0007", "52.212-4", "tier-1", and PII
/// placeholders such as `<EMAIL_1>`), sorted and
/// de-duplicated. The semantic cache requires an exact match on these: "approve under $1,000" and
/// "approve under $10,000" embed almost identically but must never share an answer.
pub fn key_terms(masked_query: &str) -> String {
    let mut terms: Vec<String> = masked_query
        .split(|c: char| c.is_whitespace() || matches!(c, '?' | '!' | '(' | ')' | '"'))
        .map(|t| {
            t.trim_matches(|c: char| !c.is_alphanumeric())
                .replace(',', "")
                .to_lowercase()
        })
        .filter(|t| t.chars().any(|c| c.is_ascii_digit()))
        .collect();
    terms.sort();
    terms.dedup();
    terms.join(" ")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn answer_key_is_scope_version_prompt_and_tier_bound() {
        let base = answer_key("What is the hotel cap?", "scopeA", 3, "v1", "cheap");
        assert_eq!(
            base,
            answer_key("  what is the HOTEL cap ", "scopeA", 3, "v1", "cheap")
        );
        assert_ne!(
            base,
            answer_key("What is the hotel cap?", "scopeB", 3, "v1", "cheap")
        );
        assert_ne!(
            base,
            answer_key("What is the hotel cap?", "scopeA", 4, "v1", "cheap")
        );
        assert_ne!(
            base,
            answer_key("What is the hotel cap?", "scopeA", 3, "v2", "cheap")
        );
        assert_ne!(
            base,
            answer_key("What is the hotel cap?", "scopeA", 3, "v1", "expensive")
        );
        assert!(base.starts_with("ans:") && base.len() == 4 + 64);
    }

    #[test]
    fn fields_are_length_prefixed() {
        assert_ne!(digest(&["x|y", "z"]), digest(&["x", "y|z"]));
        assert_ne!(digest(&["ab", ""]), digest(&["a", "b"]));
    }

    #[test]
    fn embedding_and_retrieval_keys_use_the_exact_query() {
        assert_ne!(
            embedding_key("m", "Hotel cap?"),
            embedding_key("m", "hotel cap")
        );
        assert_ne!(
            retrieval_key("q", "s", 1, "40/40/20"),
            retrieval_key("q", "s", 1, "40/40/10")
        );
    }

    #[test]
    fn key_terms_keep_numbers_ids_and_placeholders() {
        assert_eq!(
            key_terms("Who approves USD 15,000 for C-0007 in tier-1 cities, mail <EMAIL_1>?"),
            "15000 c-0007 email_1 tier-1"
        );
        assert_ne!(
            key_terms("approve under $1,000"),
            key_terms("approve under $10,000")
        );
        assert_eq!(key_terms("What is the travel policy?"), "");
    }
}
