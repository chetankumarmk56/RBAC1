"""Application settings, loaded from backend/.env."""

from pathlib import Path

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
    claude_model: str = "claude-opus-5"

    # Fallback provider, used when a Claude call fails. Leave the key blank to disable.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-3.1-flash-lite"
    llm_fallback_enabled: bool = True

    jwt_secret: str = "change-me-to-a-long-random-string"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 480

    seed_password: str = "password123"

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
