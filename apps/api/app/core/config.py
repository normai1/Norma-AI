from functools import lru_cache
from pathlib import Path

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchored to this file, not the working directory, so the same .env is found
# whether a command is run from the repository root or from apps/api. Both
# candidates are listed because the API is its own root inside the container
# (/app) while the env file sits at the repository root on a developer machine.
# Later entries win, and a missing file is ignored.
_API_DIR = Path(__file__).resolve().parents[2]
_ENV_FILES = (
    _API_DIR / ".env",
    _API_DIR.parent.parent / ".env",
)

# Placeholders shipped in .env.example and the field defaults. They are public,
# so a token signed with one is forgeable by anyone who can read the repository.
PLACEHOLDER_SECRET_KEYS = frozenset(
    {
        "change-me-in-production",
        "replace-this-in-production",
    }
)

# HS256 derives a 256-bit MAC, so a shorter key adds no strength.
MIN_SECRET_KEY_LENGTH = 32


class Settings(BaseSettings):
    """
    Application configuration.

    Values are loaded from environment variables and/or .env.
    """

    model_config = SettingsConfigDict(
        env_file=_ENV_FILES,
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

    # Tests create and drop schema, so they must never point at the dev
    # database. Defaults to the host-published port, since pytest runs on the
    # host while the app runs inside Compose.
    test_database_url: str = Field(
        default=("postgresql+asyncpg://norma:norma@localhost:5432/norma_test")
    )

    # ------------------------------------------------------------------
    # Redis
    # ------------------------------------------------------------------

    redis_url: str = "redis://localhost:6379/0"

    # A dedicated database index, flushed between tests. Host-published port for
    # the same reason as test_database_url.
    test_redis_url: str = "redis://localhost:6379/15"

    access_token_expire_minutes: int = 15

    refresh_token_expire_days: int = 30

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    secret_key: str = "change-me-in-production"

    jwt_algorithm: str = "HS256"

    # Authenticates apps/voice's service-to-service calls (item 20b) - there
    # is no user session inside a live call for a JWT to belong to. Separate
    # from secret_key: a leaked internal secret should not also compromise
    # user session tokens, and vice versa.
    internal_api_secret: str = "change-me-in-production"

    # ------------------------------------------------------------------
    # CORS
    # ------------------------------------------------------------------

    cors_origins: str = "http://localhost:3000"

    # Number of trusted proxies in front of the API. 0 means the app is reached
    # directly and X-Forwarded-For is ignored entirely. Behind Cloudflare and
    # Render this must match the real chain, because the value decides which
    # X-Forwarded-For hop is believed. Counting from the right is what stops a
    # client spoofing its own address by sending a prefilled header.
    trusted_proxy_count: int = 0

    # ------------------------------------------------------------------
    # Speech
    # ------------------------------------------------------------------

    # "mock" so the test suite and a fresh checkout can never reach a paid
    # provider without deliberately configuring one. Feature 9b adds
    # "elevenlabs" as a valid value.
    stt_provider: str = "mock"
    tts_provider: str = "mock"

    # Empty by default; the "elevenlabs" provider branch refuses to construct
    # without a real key rather than failing mid-call.
    elevenlabs_api_key: str = ""

    # ------------------------------------------------------------------
    # Embeddings
    # ------------------------------------------------------------------

    # "mock" for the same reason stt_provider/tts_provider default to it - a
    # fresh checkout and the test suite must never reach a paid provider
    # without deliberately configuring one.
    embedding_provider: str = "mock"

    # Empty by default; the "openai" provider branch refuses to construct
    # without a real key rather than failing mid-call.
    openai_api_key: str = ""

    embedding_model: str = "text-embedding-3-small"

    # Must match the actual dimension the configured model/provider
    # produces, and the chunks table's vector(1536) column width - never
    # "fixed" by truncating or padding a mismatched vector (CLAUDE.md
    # section 6.4).
    embedding_dimension: int = 1536

    # ------------------------------------------------------------------
    # Storage
    # ------------------------------------------------------------------

    # "local" (not "mock") is the default: unlike speech, local disk storage
    # is free and needs no credentials, so a fresh checkout can exercise real
    # file storage end to end. The test suite gets "mock" via a dependency
    # override in conftest.py, not by changing this default.
    storage_provider: str = "local"

    # Resolved under the API's working directory - already visible on the
    # host via the existing ./apps/api:/app bind mount, so no separate Docker
    # volume is needed for local development persistence.
    local_storage_dir: str = "data/uploads"

    # Empty by default; the "s3" provider branch refuses to construct without
    # all four set, rather than failing mid-upload.
    aws_region: str = ""
    aws_s3_bucket: str = ""
    aws_access_key_id: str = ""
    aws_secret_access_key: str = ""

    @model_validator(mode="after")
    def _reject_insecure_secret_key(self) -> "Settings":
        """
        Refuse to start outside development with a guessable signing key.
        """

        # A missing ENVIRONMENT is treated as unsafe rather than as development.
        # The default exists for convenience, and relying on it would skip this
        # check for exactly the deploy that configured nothing at all.
        declared_development = (
            "environment" in self.model_fields_set
            and self.environment == "development"
        )

        if declared_development:
            return self

        # Naming the default here would misdirect: an unset ENVIRONMENT is the
        # very reason this check is running, so it must not be reported as if
        # development had been chosen.
        where = (
            f"for ENVIRONMENT={self.environment}"
            if "environment" in self.model_fields_set
            else "because ENVIRONMENT is not set, which is treated as unsafe"
        )

        if self.secret_key in PLACEHOLDER_SECRET_KEYS:
            raise ValueError(
                f"SECRET_KEY is still a placeholder. Set a unique random value {where}."
            )

        if len(self.secret_key) < MIN_SECRET_KEY_LENGTH:
            raise ValueError(
                f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} "
                f"characters {where}."
            )

        if self.internal_api_secret in PLACEHOLDER_SECRET_KEYS:
            raise ValueError(
                "INTERNAL_API_SECRET is still a placeholder. Set a unique "
                f"random value {where}."
            )

        if len(self.internal_api_secret) < MIN_SECRET_KEY_LENGTH:
            raise ValueError(
                f"INTERNAL_API_SECRET must be at least {MIN_SECRET_KEY_LENGTH} "
                f"characters {where}."
            )

        return self

    @property
    def cors_origin_list(self) -> list[str]:
        return [
            origin.strip() for origin in self.cors_origins.split(",") if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
