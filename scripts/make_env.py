"""Create .env from .env.example with fresh random secrets. Never overwrites an existing .env.

    python3 scripts/make_env.py
"""

import base64
import os
import re
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    env, example = ROOT / ".env", ROOT / ".env.example"
    if env.exists():
        print(".env exists, leaving it alone")
        return 0
    pg_password = secrets.token_urlsafe(24)
    valkey_password = secrets.token_urlsafe(24)
    values = {
        "PII_ENCRYPTION_KEY": base64.b64encode(os.urandom(32)).decode(),
        "CONFIRMATION_HMAC_KEY": base64.b64encode(os.urandom(32)).decode(),
        "INTERNAL_API_TOKEN": secrets.token_urlsafe(32),
        "POSTGRES_PASSWORD": pg_password,
        "DATABASE_URL": f"postgresql://acme:{pg_password}@localhost:5432/procurement",
        "VALKEY_PASSWORD": valkey_password,
        "VALKEY_URL": f"redis://:{valkey_password}@localhost:6379",
    }
    text = example.read_text()
    for key, value in values.items():
        text = re.sub(rf"(?m)^{key}=.*$", f"{key}={value}", text)
    env.write_text(text)
    env.chmod(0o600)
    print("wrote .env with fresh secrets — add the API keys you have")
    return 0


if __name__ == "__main__":
    sys.exit(main())
