"""Token verification for the MCP servers and the brain (mirrors gateway/crates/auth).

- OBO JWT: RS256, issuer = gateway, audience = "mcp", 60 s lifetime, `typ = "obo"`. Verified with the
  gateway's PUBLIC key only — an MCP server can check *who* the agent acts for but can't mint tokens.
- Confirmation token: `base64url(json grant) "." base64url(HMAC-SHA256(json))`, minted by the gateway
  only after a human clicked Confirm. The MAC is checked over the received bytes, so Python never has
  to reproduce Rust's JSON serialization.
- Canonical JSON (sorted keys, compact) is what payload hashes are computed over.
"""

import base64
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import jwt

from common.settings import get_settings

ISSUER = "procurement-copilot-gateway"


class AuthError(Exception):
    """Raised for any invalid, expired or mismatched token. Message is safe to log, not to show."""


@dataclass(frozen=True)
class Identity:
    sub: str
    name: str
    role: str
    dept: str
    spend_limit: float
    approval_limit: float
    request_id: str | None = None

    def opa_user(self) -> dict[str, Any]:
        return {
            "id": self.sub,
            "role": self.role,
            "dept": self.dept,
            "spend_limit": self.spend_limit,
            "approval_limit": self.approval_limit,
        }


@lru_cache
def _public_key() -> str:
    return get_settings().jwt_public_key.read_text()


def verify_obo(token: str) -> Identity:
    try:
        c = jwt.decode(
            token,
            _public_key(),
            algorithms=["RS256"],
            audience="mcp",
            issuer=ISSUER,
            leeway=5,
            options={"require": ["exp", "iat", "aud", "iss"]},
        )
    except jwt.PyJWTError as e:
        raise AuthError(f"invalid OBO token: {type(e).__name__}") from e
    if c.get("typ") != "obo":
        raise AuthError("not an OBO token")
    return Identity(
        sub=c["sub"],
        name=c["name"],
        role=c["role"],
        dept=c["dept"],
        spend_limit=float(c["spend_limit"]),
        approval_limit=float(c["approval_limit"]),
        request_id=c.get("rid"),
    )


def bearer(headers: Any) -> str:
    """Extract a bearer token from a headers mapping (case-insensitive)."""
    value = ""
    if headers:
        value = headers.get("authorization") or headers.get("Authorization") or ""
    if not value.startswith("Bearer "):
        raise AuthError("missing bearer token")
    return str(value[len("Bearer ") :])


def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def sha256_hex(data: str | bytes) -> str:
    return hashlib.sha256(data.encode() if isinstance(data, str) else data).hexdigest()


def _b64d(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


@dataclass(frozen=True)
class ConfirmationGrant:
    proposal_id: str
    user_id: str
    payload_hash: str
    exp: int
    nonce: str


def verify_confirmation(token: str) -> ConfirmationGrant:
    key_b64 = get_settings().confirmation_hmac_key.get_secret_value()
    if not key_b64:
        raise AuthError("CONFIRMATION_HMAC_KEY not configured")
    try:
        body_b64, sig_b64 = token.split(".", 1)
        body, sig = _b64d(body_b64), _b64d(sig_b64)
    except ValueError as e:
        raise AuthError("malformed confirmation token") from e
    expected = hmac.new(base64.b64decode(key_b64), body, hashlib.sha256).digest()
    if not hmac.compare_digest(expected, sig):
        raise AuthError("bad confirmation signature")
    g = json.loads(body)
    grant = ConfirmationGrant(g["proposal_id"], g["user_id"], g["payload_hash"], int(g["exp"]), g["nonce"])
    if grant.exp < time.time():
        raise AuthError("confirmation expired")
    return grant
