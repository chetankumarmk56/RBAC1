"""Application settings, loaded from backend/.env."""

from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://rbac:rbac@localhost:5432/rbac_poc"

    anthropic_api_key: str = ""
    # The three Claude tiers a role can be granted. Which of them a given role may
    # actually use is stored in PostgreSQL — see rbac/model_catalog.py.
    claude_opus_model: str = "claude-opus-5"
    claude_sonnet_model: str = "claude-sonnet-5"
    claude_haiku_model: str = "claude-haiku-4-5-20251001"

    # Fallback provider, used when a Claude call fails. Leave the key blank to disable.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    llm_fallback_enabled: bool = True

    jwt_secret: str = "change-me-to-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    seed_password: str = "password123"

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @field_validator("database_url")
    @classmethod
    def _use_psycopg3_driver(cls, value: str) -> str:
        """Accept a hosting provider's connection string unchanged.

        Render, Heroku and friends hand out `postgres://…` or `postgresql://…`.
        Either makes SQLAlchemy load psycopg2, which this project does not install
        — only psycopg3. Rewriting the scheme here means the URL can be pasted
        straight from the dashboard.
        """
        for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://"):
            if value.startswith(prefix):
                return value
        for prefix in ("postgresql://", "postgres://"):
            if value.startswith(prefix):
                return "postgresql+psycopg://" + value[len(prefix) :]
        return value

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
