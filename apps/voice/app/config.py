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

# eleven_multilingual_v2 (the shared adapter's own internal default) measured
# 1.1-2.1s time-to-first-byte in this environment - most of a whole turn's
# latency budget spent on TTS alone. eleven_flash_v2_5 is ElevenLabs' own
# low-latency model, measured at ~330-380ms first-byte for the same text
# (English and Hinglish both), a real product-latency win CLAUDE.md's
# section 37 puts above quality tradeoffs for exactly this reason.
TTS_MODEL_ID = os.environ.get("TTS_MODEL_ID", "eleven_flash_v2_5")

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

GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")

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
# Raised from the original "2" (item 20g): against a real, rate-limit-prone
# provider, two bad turns in a row was easy to hit from ordinary transient
# trouble and ended otherwise-healthy test calls.
MAX_CONSECUTIVE_LLM_FAILURES = int(os.environ.get("MAX_CONSECUTIVE_LLM_FAILURES", "5"))

# How many times the STT stream reconnects (a fresh provider.stream() call)
# after a SpeechProviderError before giving up and triggering session
# failover. A bounded reconnect, not a full replay of audio already
# in-flight when the old stream broke - see SpeechToTextProcessor's
# docstring for why a full reconnect-with-replay stays out of scope.
MAX_STT_STREAM_RETRIES = int(os.environ.get("MAX_STT_STREAM_RETRIES", "2"))

# How many times a *cleanly closed* STT stream is reconnected while the call
# is still live. Separate from, and far larger than, MAX_STT_STREAM_RETRIES:
# an error is a sign something is wrong, but a live provider ending its own
# stream is routine - measured against ElevenLabs' realtime STT closing after
# a few minutes of a healthy session, and again seconds into one. Either way
# the caller must keep being heard for the whole call, so this budget is
# sized for "a long call", not "something is broken".
MAX_STT_STREAM_RECONNECTS = int(os.environ.get("MAX_STT_STREAM_RECONNECTS", "50"))

# Brief pause before reconnecting a closed stream, so a provider refusing
# connections outright can never become a hot loop.
STT_RECONNECT_DELAY_SECONDS = float(os.environ.get("STT_RECONNECT_DELAY_SECONDS", "0.25"))

# Ceiling on that pause as it backs off. Long enough to stop hammering a
# provider that keeps closing, short enough that a caller is never left
# untranscribed for long once it recovers.
MAX_STT_RECONNECT_DELAY_SECONDS = float(
    os.environ.get("MAX_STT_RECONNECT_DELAY_SECONDS", "2.0")
)

# Whether a genuine final transcript arriving mid-reply also counts as an
# interruption, on top of caller_speech_started's VAD speech onset. That
# onset is edge-triggered, and the assistant's own playback coming back in
# through an open mic (a speaker setup with no headphones) can hold VAD
# "speaking" across the moment the caller actually starts talking, so the
# edge never fires and the reply plays to the end - reported repeatedly from
# real use. Transcribed words do not depend on that edge at all. Kept as a
# switch because the guards that keep it from firing on the assistant's own
# echo are heuristic (see TTSProcessor._handle_transcript): if it ever cuts
# a reply short in a real deployment, this turns it off without a redeploy.
BARGE_IN_ON_TRANSCRIPT = os.environ.get("BARGE_IN_ON_TRANSCRIPT", "true").lower() == "true"

# How much of a mid-reply transcript's wording must already appear in the
# text being compared against for it to be treated as that text coming back
# rather than the caller genuinely speaking. High enough that a real
# interruption sharing a few ordinary words ("okay", "so") still counts as
# an interruption; low enough that an imperfect transcription of the
# assistant's own sentence still reads as its echo.
ECHO_WORD_OVERLAP_RATIO = float(os.environ.get("ECHO_WORD_OVERLAP_RATIO", "0.6"))

# Item 21a: verifies the voice-session ticket apps/api issues for a browser
# test call. Shared with apps/api via docker-compose.yml's env_file - both
# planes must agree on the same secret/algorithm for a ticket to ever
# validate. No default for SECRET_KEY: an empty or missing value must fail
# ticket verification loudly, never fall back to a guessable default.
SECRET_KEY = os.environ.get("SECRET_KEY", "")
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
