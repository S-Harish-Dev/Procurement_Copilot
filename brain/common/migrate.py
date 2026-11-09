"""Minimal forward-only SQL migration runner.

Applies db/migrations/NNNN_*.sql in order, each in its own transaction, and
records a checksum so an edited, already-applied migration fails loudly instead
of silently drifting.

    uv run python -m common.migrate
"""

import hashlib
import sys

import psycopg

from common.settings import REPO_ROOT, get_settings

MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"


def main() -> int:
    files = sorted(MIGRATIONS_DIR.glob("[0-9][0-9][0-9][0-9]_*.sql"))
    with psycopg.connect(get_settings().database_url, autocommit=True) as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS schema_migrations (
                   name TEXT PRIMARY KEY, sha256 TEXT NOT NULL,
                   applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"""
        )
        applied: dict[str, str] = dict(conn.execute("SELECT name, sha256 FROM schema_migrations").fetchall())
        for path in files:
            sql = path.read_text()
            digest = hashlib.sha256(sql.encode()).hexdigest()
            if path.name in applied:
                if applied[path.name] != digest:
                    print(f"ERROR: {path.name} was modified after being applied", file=sys.stderr)
                    return 1
                continue
            with conn.transaction():
                conn.execute(sql.encode())  # trusted DDL file from the repo, not user input
                conn.execute(
                    "INSERT INTO schema_migrations (name, sha256) VALUES (%s, %s)", (path.name, digest)
                )
            print(f"applied {path.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
