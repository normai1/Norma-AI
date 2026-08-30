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
