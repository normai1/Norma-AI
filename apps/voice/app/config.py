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

ELEVENLABS_API_KEY = os.environ.get("ELEVENLABS_API_KEY", "")

# Reaches apps/api by its Compose service name - only resolves inside the
# Compose network, never from the host.
API_INTERNAL_URL = os.environ.get("API_INTERNAL_URL", "http://api:8000")

INTERNAL_API_SECRET = os.environ.get("INTERNAL_API_SECRET", "")
