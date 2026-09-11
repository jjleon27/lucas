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
    storage_backend: str = "local"           # "local" | "s3" | "blob"
    local_storage_dir: str = "./uploads"
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""
    aws_region: str = "us-east-1"
    aws_bucket: str = ""
    # Vercel Blob: the `vercel_blob` package reads BLOB_READ_WRITE_TOKEN from the
    # env directly (set automatically when a Blob store is linked to the project).

    # AI
    # Which provider to prefer. Leave blank to auto-pick the first one with a key set.
    # Options: "openai" | "anthropic" | "gemini"
    ai_provider: str = ""

    openai_api_key: str = ""
    openai_model: str = "gpt-4o-mini"
    openai_vision_model: str = "gpt-4o"  # /upload (recibos + cartolas, prompt JSON completo) — 2-8s/img. gpt-4o elegido 2026-09-11 tras comparar con gpt-4.1/gpt-5/gpt-5-mini (ver docs/PLAN_split_v3.md).
    openai_vision_model_bill: str = "gpt-5.6-luna"  # /bills/{id}/ocr (split de cuentas, prompt de texto liviano). gpt-4.1-mini (default hasta hoy) fallaba en una boleta real con columna de precios desalineada (boleta "Bar La Providencia") — se probó TODA la escala de modelos de barato a caro (gpt-5-nano, gpt-4.1-nano, gpt-4o-mini, gpt-4.1-mini, gpt-4o, gpt-4.1, gpt-5-mini) y todos fallaron igual; solo la familia gpt-5.6 (luna/sol/terra) la lee bien. luna es la más rápida del grupo (~5-15s) y la única probada contra las 9 boletas del eval completo.
    openai_vision_model_fallback: str = "gpt-5-mini"  # escalation target (ambos flujos) when the fast model's read doesn't reconcile (missed/misread items on hard receipts — bar tabs with many repeated line items) — slow but careful, only pays the cost on the receipts that actually need it

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
