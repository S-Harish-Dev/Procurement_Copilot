//! OPA client and the retrieval scope derived from its decision.
//!
//! One HTTP call (`POST /v1/data/acme/authz`) returns the whole decision document. If OPA is
//! unreachable the gateway **fails closed**: retrieval is limited to `public` documents and
//! every non-read action is denied — never "allow because the policy engine is down".

use std::collections::BTreeMap;
use std::time::Duration;

use auth::Identity;
use serde::{Deserialize, Serialize};
use serde_json::{Value, json};
use sha2::{Digest, Sha256};

#[derive(Debug, thiserror::Error)]
pub enum PolicyError {
    #[error("policy engine unavailable: {0}")]
    Unavailable(String),
    #[error("malformed policy response: {0}")]
    Malformed(String),
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
pub struct Decision {
    #[serde(default)]
    pub allow: bool,
    #[serde(default)]
    pub reasons: Vec<String>,
    /// classification -> departments ("*" = any). BTreeMap so serialization is canonical.
    #[serde(default)]
    pub doc_grants: BTreeMap<String, Vec<String>>,
    #[serde(default)]
    pub approval_chain: Vec<String>,
    #[serde(default)]
    pub requires_confirmation: bool,
}

/// What a user may retrieve, plus a stable hash used to partition every cache by permission scope.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct Scope {
    pub grants: BTreeMap<String, Vec<String>>,
    pub hash: String,
    /// True when OPA was unreachable and this is the fail-closed public-only scope.
    pub degraded: bool,
}

impl Scope {
    pub fn from_grants(
        role: &str,
        mut grants: BTreeMap<String, Vec<String>>,
        degraded: bool,
    ) -> Self {
        for depts in grants.values_mut() {
            depts.sort();
            depts.dedup();
        }
        // scope_hash = sha256(role | canonical grants): two users with identical visibility share
        // cache entries; any difference in visibility gives a different key (no cross-role leaks).
        let canonical = serde_json::to_string(&grants).expect("grants serialize");
        let digest = Sha256::digest(format!("{role}|{canonical}").as_bytes());
        let hash = digest.iter().map(|b| format!("{b:02x}")).collect();
        Self {
            grants,
            hash,
            degraded,
        }
    }

    /// Fail-closed scope used when OPA is down: public documents only.
    pub fn public_only(role: &str) -> Self {
        Self::from_grants(
            role,
            BTreeMap::from([("public".to_owned(), vec!["*".to_owned()])]),
            true,
        )
    }

    pub fn allows(&self, classification: &str, department: &str) -> bool {
        self.grants
            .get(classification)
            .is_some_and(|d| d.iter().any(|x| x == "*" || x == department))
    }
}

#[derive(Clone)]
pub struct OpaClient {
    http: reqwest::Client,
    url: String,
}

impl OpaClient {
    pub fn new(base_url: &str, timeout: Duration) -> Self {
        let http = reqwest::Client::builder()
            .timeout(timeout)
            .build()
            .expect("reqwest client");
        Self {
            http,
            url: format!("{}/v1/data/acme/authz", base_url.trim_end_matches('/')),
        }
    }

    pub async fn decide(
        &self,
        user: &Identity,
        action: &str,
        resource: Value,
    ) -> Result<Decision, PolicyError> {
        let input = json!({
            "input": {
                "user": {
                    "id": user.sub, "role": user.role.as_str(), "dept": user.dept,
                    "spend_limit": user.spend_limit, "approval_limit": user.approval_limit,
                },
                "action": action,
                "resource": resource,
            }
        });
        let resp = self
            .http
            .post(&self.url)
            .json(&input)
            .send()
            .await
            .map_err(|e| PolicyError::Unavailable(e.to_string()))?;
        if !resp.status().is_success() {
            return Err(PolicyError::Unavailable(format!(
                "status {}",
                resp.status()
            )));
        }
        let body: Value = resp
            .json()
            .await
            .map_err(|e| PolicyError::Malformed(e.to_string()))?;
        // OPA returns {} (no "result") when the package isn't loaded — treat as unavailable.
        let result = body
            .get("result")
            .ok_or_else(|| PolicyError::Unavailable("no result".into()))?;
        serde_json::from_value(result.clone()).map_err(|e| PolicyError::Malformed(e.to_string()))
    }

    /// Retrieval scope for a user; falls back to public-only if OPA is unreachable.
    pub async fn retrieval_scope(&self, user: &Identity) -> Scope {
        match self.decide(user, "retrieve", json!({})).await {
            Ok(d) => Scope::from_grants(user.role.as_str(), d.doc_grants, false),
            Err(e) => {
                tracing::warn!(error = %e, "OPA unavailable: failing closed to public-only scope");
                Scope::public_only(user.role.as_str())
            }
        }
    }

    pub async fn health(&self) -> bool {
        let health = self.url.replace("/v1/data/acme/authz", "/health");
        self.http
            .get(health)
            .send()
            .await
            .is_ok_and(|r| r.status().is_success())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use auth::Role;
    use wiremock::matchers::{method, path};
    use wiremock::{Mock, MockServer, ResponseTemplate};

    fn user(role: Role, dept: &str) -> Identity {
        Identity {
            sub: "u".into(),
            name: "U".into(),
            role,
            dept: dept.into(),
            spend_limit: 0.0,
            approval_limit: 0.0,
        }
    }

    #[test]
    fn scope_hash_is_canonical_and_role_scoped() {
        let a = Scope::from_grants(
            "employee",
            BTreeMap::from([("internal".into(), vec!["all".into(), "IT".into()])]),
            false,
        );
        let b = Scope::from_grants(
            "employee",
            BTreeMap::from([(
                "internal".into(),
                vec!["IT".into(), "all".into(), "IT".into()],
            )]),
            false,
        );
        assert_eq!(a.hash, b.hash, "order/duplicates must not change the hash");
        let c = Scope::from_grants("manager", a.grants.clone(), false);
        assert_ne!(
            a.hash, c.hash,
            "different role -> different cache partition"
        );
    }

    #[test]
    fn allows_respects_wildcards_and_departments() {
        let s = Scope::from_grants(
            "employee",
            BTreeMap::from([
                ("public".into(), vec!["*".into()]),
                ("internal".into(), vec!["IT".into(), "all".into()]),
            ]),
            false,
        );
        assert!(s.allows("public", "Finance"));
        assert!(s.allows("internal", "IT"));
        assert!(!s.allows("internal", "HR"));
        assert!(!s.allows("confidential", "Finance"));
    }

    #[tokio::test]
    async fn opa_down_fails_closed_to_public_only() {
        let client = OpaClient::new("http://127.0.0.1:9", Duration::from_millis(200));
        let scope = client.retrieval_scope(&user(Role::Admin, "IT")).await;
        assert!(scope.degraded);
        assert!(scope.allows("public", "x"));
        assert!(
            !scope.allows("restricted", "all"),
            "admin loses restricted docs when OPA is down"
        );
    }

    #[tokio::test]
    async fn parses_decision_document() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .and(path("/v1/data/acme/authz"))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({"result": {
                "allow": true, "reasons": [], "doc_grants": {"public": ["*"], "internal": ["IT", "all"]},
                "approval_chain": [], "requires_confirmation": false
            }})))
            .mount(&server)
            .await;
        let client = OpaClient::new(&server.uri(), Duration::from_secs(1));
        let scope = client.retrieval_scope(&user(Role::Employee, "IT")).await;
        assert!(!scope.degraded);
        assert!(scope.allows("internal", "IT") && !scope.allows("internal", "HR"));
    }

    #[tokio::test]
    async fn missing_result_is_unavailable_not_allow() {
        let server = MockServer::start().await;
        Mock::given(method("POST"))
            .respond_with(ResponseTemplate::new(200).set_body_json(json!({})))
            .mount(&server)
            .await;
        let client = OpaClient::new(&server.uri(), Duration::from_secs(1));
        let r = client
            .decide(&user(Role::Admin, "IT"), "approve_pr", json!({}))
            .await;
        assert!(matches!(r, Err(PolicyError::Unavailable(_))));
    }
}
