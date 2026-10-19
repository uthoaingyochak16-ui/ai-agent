from __future__ import annotations

import base64
from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    env: str = "dev"
    database_url: str = "postgresql+asyncpg://app:app@localhost:5432/assistant"
    redis_url: str = "redis://localhost:6379/0"

    # Secrets: no defaults on purpose, the app refuses to start without them.
    secret_key: str            # signs admin JWTs
    encryption_key: str        # base64, 32 bytes, AES-GCM key for phone/email/tokens
    hmac_key: str              # HMAC key for phone/email lookup hashes
    jwt_ttl_minutes: int = 720

    # Meta
    meta_app_secret: str = ""      # default app secret for X-Hub-Signature-256
    meta_verify_token: str = ""    # default webhook verify token
    graph_base_url: str = "https://graph.facebook.com"
    graph_api_version: str = "v21.0"   # check Meta's changelog and bump when needed
    http_timeout_s: float = 15.0
    http_max_retries: int = 3

    public_base_url: str = "http://localhost:8000"   # shown on the Channels page as the webhook base
    embedding_dim: int = 1024          # pgvector column size; changing it requires a migration + re-embedding
    default_phone_region: str = "BD"
    upload_dir: str = "./uploads"
    cors_origins: str = "http://localhost:5173"

    # Single sign-on from the main application (docs/INTEGRATION.md). Empty secret = SSO off.
    sso_shared_secret: str = ""        # HS256 key the main app signs its short-lived login tokens with
    sso_issuer: str = ""               # expected "iss" claim (e.g. "main-app"); empty = not checked
    sso_default_role: str = "agent"    # role when the token names none (admin | agent)
    sso_max_age_s: int = 300           # a token older than this is refused, and each token works once
    log_level: str = "INFO"

    @field_validator("encryption_key")
    @classmethod
    def _check_enc_key(cls, v: str) -> str:
        try:
            raw = base64.b64decode(v)
        except Exception as exc:  # noqa: BLE001
            raise ValueError("ENCRYPTION_KEY must be base64") from exc
        if len(raw) != 32:
            raise ValueError("ENCRYPTION_KEY must decode to exactly 32 bytes")
        return v

    @field_validator("hmac_key", "secret_key")
    @classmethod
    def _check_len(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("must be at least 32 characters")
        return v

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
