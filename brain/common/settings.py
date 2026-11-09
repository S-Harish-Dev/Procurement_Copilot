"""Process configuration, read from environment / repo-root .env."""

from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    database_url: str = "postgresql://acme:acme-dev-only@localhost:5432/procurement"
    qdrant_url: str = "http://localhost:6333"
    pii_encryption_key: SecretStr = SecretStr("")
    hf_token: SecretStr = SecretStr("")

    data_dir: Path = REPO_ROOT / "data"
    models_dir: Path = REPO_ROOT / "models"

    # --- action path (M5) ---
    opa_url: str = "http://localhost:8181"
    jwt_public_key: Path = REPO_ROOT / "infra" / "keys" / "jwt-public.pem"
    confirmation_hmac_key: SecretStr = SecretStr("")
    internal_api_token: SecretStr = SecretStr("")
    gateway_internal_url: str = "http://127.0.0.1:8081"
    mcp_procurement_url: str = "http://127.0.0.1:8101/mcp"
    mcp_notify_url: str = "http://127.0.0.1:8102/mcp"
    brain_bind: str = "127.0.0.1:50051"
    smtp_host: str = "localhost"
    smtp_port: int = 1025
    po_dir: Path = REPO_ROOT / "data" / "po"
    prompts_dir: Path = REPO_ROOT / "prompts"

    @field_validator("jwt_public_key", "po_dir", "prompts_dir", "data_dir", "models_dir")
    @classmethod
    def _repo_relative(cls, v: Path) -> Path:
        # .env holds repo-relative paths (e.g. infra/keys/jwt-public.pem); processes run from brain/.
        return v if v.is_absolute() else REPO_ROOT / v


@lru_cache
def get_settings() -> Settings:
    return Settings()
