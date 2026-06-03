"""
Centralised settings, loaded from environment variables via pydantic-settings.
Everything else in the app imports `settings` from here — no `os.getenv` scattered
around the codebase.
"""
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Core
    database_url: str = "postgresql://lucas:lucas@localhost:5432/lucas"
    jwt_secret: str = "dev-secret-change-me"
    jwt_expire_minutes: int = 60 * 24 * 7  # 7 days
    jwt_algorithm: str = "HS256"

    # Storage
    storage_backend: str = "local"           # "local" | "s3"
    local_storage_dir: str = "./uploads"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    aws_bucket: str = ""

    # AI
    # Which provider to prefer. Leave blank to auto-pick the first one with a key set.
    # Options: "openai" | "anthropic" | "gemini"
    ai_provider: str = ""

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_vision_model: str = "gpt-4o-2024-11-20"  # full model for receipt parsing

    anthropic_api_key: str = ""
    anthropic_model: str = "claude-haiku-4-5-20251001"

    google_api_key: str = ""
    google_model: str = "gemini-1.5-flash"

    # Social login (leave blank to disable)
    google_client_id: str = ""     # e.g. "12345-abc.apps.googleusercontent.com"
    # Quick passwordless mode — on for local dev, turn OFF in production and replace
    # with a magic-link email flow.
    allow_passwordless: bool = True

    # CORS
    cors_origins: str = "http://localhost:3000"

    model_config = SettingsConfigDict(env_file=".env", case_sensitive=False, extra="ignore")

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
