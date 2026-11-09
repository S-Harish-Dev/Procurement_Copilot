//! Authentication tokens.
//!
//! Three kinds, each with its own audience so one can never be replayed as another:
//! - **user** JWT (RS256, `aud = "gateway"`): issued at login, carried by the browser.
//! - **on-behalf-of** JWT (RS256, `aud = "mcp"`, 60 s): minted per agent run so MCP servers can
//!   verify *who* the agent acts for with the gateway's public key alone.
//! - **confirmation** token (HMAC-SHA256, one-time): minted only when a human clicks Confirm on
//!   a proposed write action. The LLM never sees the signing key, so it cannot forge one.

use base64::Engine as _;
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use hmac::{Hmac, KeyInit, Mac};
use jsonwebtoken::{Algorithm, DecodingKey, EncodingKey, Header, Validation};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use time::OffsetDateTime;

pub const ISSUER: &str = "procurement-copilot-gateway";
pub const AUD_USER: &str = "gateway";
pub const AUD_OBO: &str = "mcp";
pub const OBO_TTL_SECS: i64 = 60;

#[derive(Debug, thiserror::Error)]
pub enum AuthError {
    #[error("invalid key material: {0}")]
    Key(String),
    #[error("invalid token")]
    InvalidToken,
    #[error("token expired")]
    Expired,
    #[error("wrong token type")]
    WrongType,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Role {
    Employee,
    Manager,
    Finance,
    Admin,
}

impl Role {
    pub fn as_str(self) -> &'static str {
        match self {
            Role::Employee => "employee",
            Role::Manager => "manager",
            Role::Finance => "finance",
            Role::Admin => "admin",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum TokenType {
    User,
    Obo,
}

/// The user facts every downstream decision needs (OPA input, budget checks, UI).
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct Identity {
    pub sub: String,
    pub name: String,
    pub role: Role,
    pub dept: String,
    pub spend_limit: f64,
    pub approval_limit: f64,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct Claims {
    #[serde(flatten)]
    pub identity: Identity,
    pub typ: TokenType,
    pub iss: String,
    pub aud: String,
    pub iat: i64,
    pub exp: i64,
    /// Request id the OBO token was minted for (audit correlation). None for user tokens.
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub rid: Option<String>,
}

pub struct JwtKeys {
    encoding: EncodingKey,
    decoding: DecodingKey,
}

impl JwtKeys {
    pub fn from_pem(private_pem: &[u8], public_pem: &[u8]) -> Result<Self, AuthError> {
        Ok(Self {
            encoding: EncodingKey::from_rsa_pem(private_pem)
                .map_err(|e| AuthError::Key(e.to_string()))?,
            decoding: DecodingKey::from_rsa_pem(public_pem)
                .map_err(|e| AuthError::Key(e.to_string()))?,
        })
    }

    pub fn issue_user(&self, identity: &Identity, ttl_secs: i64) -> Result<String, AuthError> {
        self.sign(identity, TokenType::User, AUD_USER, ttl_secs, None)
    }

    pub fn issue_obo(&self, identity: &Identity, request_id: &str) -> Result<String, AuthError> {
        self.sign(
            identity,
            TokenType::Obo,
            AUD_OBO,
            OBO_TTL_SECS,
            Some(request_id.to_owned()),
        )
    }

    fn sign(
        &self,
        identity: &Identity,
        typ: TokenType,
        aud: &str,
        ttl_secs: i64,
        rid: Option<String>,
    ) -> Result<String, AuthError> {
        let now = OffsetDateTime::now_utc().unix_timestamp();
        let claims = Claims {
            identity: identity.clone(),
            typ,
            iss: ISSUER.into(),
            aud: aud.into(),
            iat: now,
            exp: now + ttl_secs,
            rid,
        };
        jsonwebtoken::encode(&Header::new(Algorithm::RS256), &claims, &self.encoding)
            .map_err(|e| AuthError::Key(e.to_string()))
    }

    /// Verify signature, issuer, audience, expiry (5 s leeway) and token type.
    pub fn verify(&self, token: &str, expected: TokenType) -> Result<Claims, AuthError> {
        let mut v = Validation::new(Algorithm::RS256);
        v.set_issuer(&[ISSUER]);
        v.set_audience(&[match expected {
            TokenType::User => AUD_USER,
            TokenType::Obo => AUD_OBO,
        }]);
        v.leeway = 5;
        let data =
            jsonwebtoken::decode::<Claims>(token, &self.decoding, &v).map_err(|e| {
                match e.kind() {
                    jsonwebtoken::errors::ErrorKind::ExpiredSignature => AuthError::Expired,
                    _ => AuthError::InvalidToken,
                }
            })?;
        if data.claims.typ != expected {
            return Err(AuthError::WrongType);
        }
        Ok(data.claims)
    }
}

// ---------------------------------------------------------------------------------------------
// Confirmation tokens
// ---------------------------------------------------------------------------------------------

type HmacSha256 = Hmac<Sha256>;

/// What a confirmation token authorises: exactly one proposal, for one user, with one payload.
#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ConfirmationGrant {
    pub proposal_id: String,
    pub user_id: String,
    pub payload_hash: String,
    pub exp: i64,
    /// Random per mint, so two confirmations of the same proposal never produce the same token.
    pub nonce: String,
}

pub struct ConfirmationSigner {
    key: Vec<u8>,
}

impl ConfirmationSigner {
    pub fn new(key: &[u8]) -> Result<Self, AuthError> {
        if key.len() < 32 {
            return Err(AuthError::Key(
                "confirmation key must be at least 32 bytes".into(),
            ));
        }
        Ok(Self { key: key.to_vec() })
    }

    fn mac(&self) -> HmacSha256 {
        // new_from_slice only fails for invalid key lengths, which HMAC does not have.
        <HmacSha256 as KeyInit>::new_from_slice(&self.key).expect("HMAC accepts any key length")
    }

    /// Token = base64url(json(grant)) "." base64url(hmac(json)).
    pub fn mint(&self, grant: &ConfirmationGrant) -> String {
        let body = serde_json::to_vec(grant).expect("grant serializes");
        let mut mac = self.mac();
        mac.update(&body);
        let sig = mac.finalize().into_bytes();
        format!(
            "{}.{}",
            URL_SAFE_NO_PAD.encode(&body),
            URL_SAFE_NO_PAD.encode(sig)
        )
    }

    /// Checks the signature (constant time) and expiry. One-time use is enforced by the
    /// caller against the database (`action_confirmations.used_at`), not here.
    pub fn verify(&self, token: &str) -> Result<ConfirmationGrant, AuthError> {
        let (body_b64, sig_b64) = token.split_once('.').ok_or(AuthError::InvalidToken)?;
        let body = URL_SAFE_NO_PAD
            .decode(body_b64)
            .map_err(|_| AuthError::InvalidToken)?;
        let sig = URL_SAFE_NO_PAD
            .decode(sig_b64)
            .map_err(|_| AuthError::InvalidToken)?;
        let mut mac = self.mac();
        mac.update(&body);
        mac.verify_slice(&sig)
            .map_err(|_| AuthError::InvalidToken)?;
        let grant: ConfirmationGrant =
            serde_json::from_slice(&body).map_err(|_| AuthError::InvalidToken)?;
        if grant.exp < OffsetDateTime::now_utc().unix_timestamp() {
            return Err(AuthError::Expired);
        }
        Ok(grant)
    }
}

/// sha256 hex — used for payload hashes and for storing token hashes (never tokens) in the DB.
pub fn sha256_hex(data: &[u8]) -> String {
    let digest = Sha256::digest(data);
    digest.iter().map(|b| format!("{b:02x}")).collect()
}

/// A fresh random nonce for confirmation grants.
pub fn new_nonce() -> String {
    uuid::Uuid::new_v4().simple().to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    const PRIV: &[u8] = include_bytes!("../testdata/test-private.pem");
    const PUB: &[u8] = include_bytes!("../testdata/test-public.pem");

    fn priya() -> Identity {
        Identity {
            sub: "priya.sharma".into(),
            name: "Priya Sharma".into(),
            role: Role::Employee,
            dept: "IT".into(),
            spend_limit: 5000.0,
            approval_limit: 0.0,
        }
    }

    #[test]
    fn user_token_roundtrip() {
        let keys = JwtKeys::from_pem(PRIV, PUB).unwrap();
        let t = keys.issue_user(&priya(), 3600).unwrap();
        let c = keys.verify(&t, TokenType::User).unwrap();
        assert_eq!(c.identity, priya());
    }

    #[test]
    fn obo_token_cannot_be_used_as_user_token_and_vice_versa() {
        let keys = JwtKeys::from_pem(PRIV, PUB).unwrap();
        let obo = keys.issue_obo(&priya(), "req-1").unwrap();
        assert!(keys.verify(&obo, TokenType::User).is_err()); // audience mismatch
        let user = keys.issue_user(&priya(), 3600).unwrap();
        assert!(keys.verify(&user, TokenType::Obo).is_err());
        assert_eq!(
            keys.verify(&obo, TokenType::Obo).unwrap().rid.as_deref(),
            Some("req-1")
        );
    }

    #[test]
    fn expired_token_rejected() {
        let keys = JwtKeys::from_pem(PRIV, PUB).unwrap();
        let t = keys.issue_user(&priya(), -60).unwrap();
        assert!(matches!(
            keys.verify(&t, TokenType::User),
            Err(AuthError::Expired)
        ));
    }

    #[test]
    fn tampered_token_rejected() {
        let keys = JwtKeys::from_pem(PRIV, PUB).unwrap();
        let t = keys.issue_user(&priya(), 3600).unwrap();
        let mut parts: Vec<&str> = t.split('.').collect();
        let forged = URL_SAFE_NO_PAD.encode(
            serde_json::to_vec(&serde_json::json!({"sub":"priya.sharma","role":"admin"})).unwrap(),
        );
        parts[1] = &forged;
        assert!(keys.verify(&parts.join("."), TokenType::User).is_err());
    }

    fn grant(exp_offset: i64) -> ConfirmationGrant {
        ConfirmationGrant {
            proposal_id: "prop-1".into(),
            user_id: "priya.sharma".into(),
            payload_hash: sha256_hex(b"{}"),
            exp: OffsetDateTime::now_utc().unix_timestamp() + exp_offset,
            nonce: new_nonce(),
        }
    }

    #[test]
    fn confirmation_roundtrip_and_forgery() {
        let s = ConfirmationSigner::new(&[7u8; 32]).unwrap();
        let g = grant(300);
        let token = s.mint(&g);
        assert_eq!(s.verify(&token).unwrap(), g);
        // Different key (e.g. a guessed one) cannot produce a valid token.
        let other = ConfirmationSigner::new(&[8u8; 32]).unwrap();
        assert!(other.verify(&token).is_err());
        // Changing the payload hash breaks the MAC.
        let (_, sig) = token.split_once('.').unwrap();
        let mut g2 = g.clone();
        g2.payload_hash = sha256_hex(b"{\"total\":999999}");
        let body = URL_SAFE_NO_PAD.encode(serde_json::to_vec(&g2).unwrap());
        assert!(s.verify(&format!("{body}.{sig}")).is_err());
    }

    #[test]
    fn expired_confirmation_rejected() {
        let s = ConfirmationSigner::new(&[7u8; 32]).unwrap();
        assert!(matches!(
            s.verify(&s.mint(&grant(-1))),
            Err(AuthError::Expired)
        ));
    }

    #[test]
    fn short_confirmation_key_rejected() {
        assert!(ConfirmationSigner::new(b"short").is_err());
    }
}
