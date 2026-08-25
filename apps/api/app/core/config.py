from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    Application configuration.

    Values are loaded from environment variables and/or .env.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application
    # ------------------------------------------------------------------

    app_name: str = "Norma AI"
    app_version: str = "0.1.0"
    environment: str = "development"
    debug: bool = False

    # ------------------------------------------------------------------
    # API
    # ------------------------------------------------------------------

    api_v1_prefix: str = "/api/v1"

    # ------------------------------------------------------------------
    # Database
    # ------------------------------------------------------------------

    database_url: str = Field(
        default=("postgresql+asyncpg://norma:norma@localhost:5432/norma")
    )

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------

    redis_url: str = "redis://localhost:6379/0"

    access_token_expire_minutes: int = 15

    refresh_token_expire_days: int = 30

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    secret_key: str = "change-me-in-production"

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------

    cors_origins: str = "http://localhost:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
