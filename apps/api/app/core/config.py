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
    # Matches apps/voice, which reads LOG_LEVEL from the environment too.
    log_level: str = "INFO"

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

    # Empty by default; the "huggingface" provider branch refuses to
    # construct without a real token rather than failing mid-call. Read from
    # HF_TOKEN (case-insensitive env matching), matching how HuggingFace's
    # own tooling names this value.
    hf_token: str = ""

    embedding_model: str = "text-embedding-3-small"

    # Must match the actual dimension the configured model/provider
    # produces, and the chunks table's vector column width - never "fixed"
    # by truncating or padding a mismatched vector (CLAUDE.md section 6.4).
    embedding_dimension: int = 1536

    # ------------------------------------------------------------------
    # Website crawling
    # ------------------------------------------------------------------
    # Sized to cover a whole small-business site rather than sample it: the
    # original 20 pages at depth 2 reached little more than a homepage and
    # its immediate links, so most of a customer's site never became
    # knowledge at all. Still bounded, deliberately - an unbounded crawl of
    # an arbitrary domain is a runaway job and an unbounded embedding bill,
    # and CLAUDE.md section 37 wants expensive ingestion bounded and out of
    # any latency-sensitive path. Raise crawl_max_pages for a genuinely
    # large site; the crawl runs in the background, so a bigger number
    # costs time and embeddings, not a blocked request.
    crawl_max_pages: int = 300
    crawl_max_depth: int = 5

    # How many chunks go to the embedding provider in one request. A whole
    # crawl used to go in a single call, which is fine for a small site and
    # impossible for a large one - a 200-page crawl made one request carrying
    # over a thousand chunks, and it timed out every time.
    embedding_batch_size: int = 32

    # A hosted embedding provider is slow often enough that a timeout in a run
    # of forty batches is expected rather than exceptional (measured: 0.4s
    # warm, over 5s cold). Without retries one of those loses the entire
    # crawl.
    embedding_max_attempts: int = 3
    embedding_retry_backoff_seconds: float = 1.0

    # ------------------------------------------------------------------
    # FAQ generation (apps/api's own background/batch text generation -
    # e.g. drafting candidate FAQ entries from an ingested knowledge
    # source - distinct from apps/voice's realtime per-turn LLM providers,
    # which serve a completely different latency budget)
    # ------------------------------------------------------------------

    # "mock" for the same reason every other provider defaults to it - a
    # fresh checkout and the test suite must never reach a paid provider
    # without deliberately configuring one.
    faq_generation_provider: str = "mock"

    # Empty by default; the "groq" provider branch refuses to construct
    # without a real key rather than failing mid-generation.
    groq_api_key: str = ""

    # A second Groq key, on a separate account, for FAQ generation only.
    #
    # Rate limits are per account, and this model's are 8,000 tokens a
    # minute and 200,000 a day. Generation and the live call loop were
    # sharing them: a document costs about 5,700 tokens a minute to process,
    # which is most of what a call needs to answer at all, and a couple of
    # 50-page PDFs spend the day's allowance outright. Callers heard "Sorry,
    # I'm having trouble responding right now" because somebody had uploaded
    # a file - which CLAUDE.md section 21 forbids outright: a caller must
    # never experience a limit event.
    #
    # Falls back to groq_api_key when unset, so a single-account setup still
    # works exactly as before.
    groq_api_key_secret: str = ""

    faq_generation_model: str = "openai/gpt-oss-120b"

    # Tokens per minute FAQ generation may spend with the provider. A large
    # document is many calls and the provider counts them against one
    # per-minute allowance, so without a budget here the second half of a
    # 50-page PDF came back 429 and was silently never read - the reported
    # "only generates 8 FAQs" for a document that should have yielded
    # dozens.
    #
    # Only the starting value: Groq reports its own limit on every response
    # and generation adopts that instead from the first call onwards. The
    # default is the limit actually observed for this model
    # (x-ratelimit-limit-tokens: 8000) rather than a larger guess, so the
    # very first window of a document does not walk straight into a refusal
    # before there is anything to adopt.
    #
    # Generation then queues itself behind the budget, waiting rather than
    # failing - affordable because it runs in the background after an upload
    # and nowhere near a live call.
    faq_generation_tokens_per_minute: int = 8_000

    # The similarity a chunk must reach to be shown to the model at all.
    #
    # Without a floor, retrieval returns its top_k nearest chunks whatever
    # their distance, so a question the knowledge cannot answer still hands
    # the model a full set of the least-bad ones - and the model sounds
    # equally confident whether they scored 0.9 or 0.5. Reported live as
    # answers that mixed unrelated chunks, invented details, and
    # contradicted the source.
    #
    # 0.62 is the midpoint of the gap measured against a real 4,035-chunk
    # crawl of cursor.com: eight questions the site answers scored 0.670 to
    # 0.837 on their best chunk, and five it cannot answer scored 0.414 to
    # 0.579. Anywhere in that gap keeps all eight and rejects all five; the
    # midpoint leaves the most room on both sides for a site whose
    # separation is narrower.
    #
    # Configurable because the right value depends on the embedding model
    # and the corpus, and because the two ways of being wrong are not
    # symmetric: too high and the assistant says it does not know something
    # it does know, which is safe and annoying; too low and it invents an
    # answer, which is the failure this exists to prevent.
    retrieval_min_score: float = 0.62

    # ------------------------------------------------------------------
    # LangSmith (retrieval tracing, item 25a)
    # ------------------------------------------------------------------

    # The switch. Tracing is on exactly when this is set, so pasting a key
    # into .env is all it takes and an environment without one pays nothing.
    langsmith_api_key: str = ""

    langsmith_project: str = "norma-retrieval"

    # Empty means the SDK's own default (US). Set it for the EU endpoint.
    langsmith_endpoint: str = ""

    # Whether the caller's question and the retrieved chunk text are sent to
    # LangSmith alongside the scores and identifiers.
    #
    # Off, and the default has to stay off. CLAUDE.md section 27 forbids
    # logging transcript text and full document contents, and shipping them
    # to a third-party service is a stronger form of the same thing: the
    # question is whatever the caller just said, and the chunks are the
    # operator's own documents. Turn it on to debug a corpus you own with
    # callers who are you, not on an environment taking real calls.
    langsmith_trace_text: bool = False

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
