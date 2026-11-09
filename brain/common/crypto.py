"""Field-level encryption for PII at rest (AES-256-GCM).

Stored layout: 12-byte random nonce || ciphertext+tag. The key comes from
PII_ENCRYPTION_KEY (base64, 32 bytes) and never touches the database, so a DB
dump alone does not expose vendor bank details.
"""

import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from common.settings import get_settings

_NONCE_LEN = 12


def _cipher() -> AESGCM:
    raw = get_settings().pii_encryption_key.get_secret_value()
    if not raw:
        raise RuntimeError("PII_ENCRYPTION_KEY is not set (see .env.example)")
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise RuntimeError("PII_ENCRYPTION_KEY must decode to 32 bytes")
    return AESGCM(key)


def encrypt(plaintext: str, *, aad: bytes = b"") -> bytes:
    """`aad` binds the ciphertext to its row/column so values can't be swapped between rows."""
    nonce = os.urandom(_NONCE_LEN)
    return nonce + _cipher().encrypt(nonce, plaintext.encode(), aad)


def decrypt(blob: bytes, *, aad: bytes = b"") -> str:
    return _cipher().decrypt(blob[:_NONCE_LEN], blob[_NONCE_LEN:], aad).decode()
