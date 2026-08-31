"""
Plain environment-variable configuration - apps/voice stays lean rather
than pulling in pydantic-settings for a handful of values, matching
apps/worker's existing minimalism.
"""

import os

# "mock" so a fresh checkout and the test suite never reach a paid provider
# without deliberately configuring one - matches apps/api's own default
# reasoning for the same setting.
STT_PROVIDER = os.environ.get("STT_PROVIDER", "mock")

# Same reasoning as STT_PROVIDER's default.
TTS_PROVIDER = os.environ.get("TTS_PROVIDER", "mock")

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")

# Reaches apps/api by its Compose service name - only resolves inside the
# Compose network, never from the host.
API_INTERNAL_URL = os.environ.get("API_INTERNAL_URL", "http://api:8000")

INTERNAL_API_SECRET = os.environ.get("INTERNAL_API_SECRET", "")

# "mock" for the same reason STT_PROVIDER defaults to it - a fresh checkout
# and the test suite must never reach a paid provider without deliberately
# configuring one.
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "mock")

# claude-haiku-4-5 class, matching CLAUDE.md section 8.1's explicit
# rejection of a frontier model in the per-turn conversation loop.
LLM_REALTIME_MODEL = os.environ.get("LLM_REALTIME_MODEL", "claude-haiku-4-5-20251001")

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Empty means "use the SDK default" - only set for a proxy or custom
# endpoint.
ANTHROPIC_BASE_URL = os.environ.get("ANTHROPIC_BASE_URL", "")

# Item 20g's resilience constants. Only the *first* token/first byte is
# guarded - a stream that already produced output is not hung - see
# app/media_session.py's TTSProcessor/LLMTurnProcessor docstrings.
LLM_FIRST_TOKEN_TIMEOUT_SECONDS = float(
    os.environ.get("LLM_FIRST_TOKEN_TIMEOUT_SECONDS", "8.0")
)
TTS_FIRST_BYTE_TIMEOUT_SECONDS = float(os.environ.get("TTS_FIRST_BYTE_TIMEOUT_SECONDS", "5.0"))

# One retry (two total attempts) before giving up on a single LLM turn or
# TTS sentence.
MAX_PROVIDER_RETRIES = int(os.environ.get("MAX_PROVIDER_RETRIES", "1"))

# How many *consecutive* fully-failed LLM turns trigger session failover -
# a single isolated blip still just gets the existing llm_error message.
MAX_CONSECUTIVE_LLM_FAILURES = int(os.environ.get("MAX_CONSECUTIVE_LLM_FAILURES", "2"))

# Item 21a: verifies the voice-session ticket apps/api issues for a browser
# test call. Shared with apps/api via docker-compose.yml's env_file - both
# planes must agree on the same secret/algorithm for a ticket to ever
# validate. No default for SECRET_KEY: an empty or missing value must fail
# ticket verification loudly, never fall back to a guessable default.
SECRET_KEY = os.environ.get("SECRET_KEY", "")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
