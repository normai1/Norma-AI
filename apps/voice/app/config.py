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

# A second realtime model to fall back to when the first is rate limited.
#
# A per-minute token quota is the one provider failure that retrying cannot
# fix, and shedding the prompt only buys one attempt. Measured on a real
# call: six turns spending 13,693 tokens in about sixty seconds against a
# limit of 8,000, and the seventh turn answered "Sorry, I'm having trouble
# responding right now."
#
# What makes a second model a real answer rather than a slower retry is that
# the quota is per model, which was measured rather than assumed: spending
# 1,500 tokens on gpt-oss-120b took its remaining allowance from 7,927 to
# 6,420 and left gpt-oss-20b's at 7,927.
#
# Empty disables it, and it is off by default: a fallback silently answering
# from a different model is a surprise nobody asked for, and the models a
# deployment is willing to speak with are the operator's decision.
LLM_FALLBACK_MODEL = os.environ.get("LLM_FALLBACK_MODEL", "")

# What LLM_REALTIME_MODEL charges, in USD per million tokens, as decimal
# strings ("0.15") so no float ever represents money (item 25b, see
# norma_shared.token_cost). Deliberately unset by default: a price is a fact
# about a vendor on a date, and shipping a guess would report the margin as
# better or worse than it is with nothing to indicate which. Unset means
# turns record their token counts with no cost, and say so once in the log.
LLM_REALTIME_INPUT_USD_PER_MTOK = os.environ.get("LLM_REALTIME_INPUT_USD_PER_MTOK", "")
LLM_REALTIME_OUTPUT_USD_PER_MTOK = os.environ.get("LLM_REALTIME_OUTPUT_USD_PER_MTOK", "")

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

# How long the caller may go on producing speech-level audio while the STT
# stream returns nothing at all before that stream is treated as deaf and
# reconnected.
#
# Reported as "assistant not working": two consecutive sessions where audio
# arrived for the full 34 seconds, several windows of it at clear speech
# level (peaks of 0.13, 0.14, 0.22, 0.28 of full scale), and the provider
# stream produced not one transcript, not one error, and never closed. The
# reconnect machinery could not help because nothing ever told it anything
# was wrong - the log showed "stt stream starting" and then nothing until
# the caller gave up and hung up.
#
# Five seconds, down from twelve. Twelve was picked for safety before there
# was any evidence about how often this fires or what it costs; there is now
# plenty of both.
#
# What it costs: the caller's utterance. A stream that has gone quiet never
# transcribes the audio sent to it, so everything said between the stream
# dying and the watchdog noticing is lost, and the caller has to say it
# again. Every second of the threshold is a second of the caller talking to
# nothing. Measured over three hours of real calls: twelve restarts, each
# costing whatever was said in the window before it.
#
# What it risks: killing a healthy but slow stream. End of speech to
# committed transcript measures 1.32s median and 1.38s worst over five
# sequential turns at realtime pacing, so five seconds is nearly four times
# the observed worst case, and a partial transcript counts as a response -
# a stream that is working at all keeps resetting the clock.
#
# It still cannot fire on a quiet caller: the check requires speech-level
# audio to have arrived *since* the last transcript.
STT_DEAF_WATCHDOG_SECONDS = float(os.environ.get("STT_DEAF_WATCHDOG_SECONDS", "5.0"))

# How often that watchdog looks. Cheap - it compares two timestamps.
STT_DEAF_WATCHDOG_POLL_SECONDS = float(
    os.environ.get("STT_DEAF_WATCHDOG_POLL_SECONDS", "2.0")
)


# How recently the VAD must have confirmed the caller speaking for a
# mid-reply transcript to count as an interruption (item 20e's barge-in).
#
# The transcriber hears the whole call and will make words out of a
# television, a passing conversation or a door closing. Without this, any of
# those cancels the reply, which the caller experiences as the assistant
# stopping mid-sentence at a noise - reported twice. Requiring the VAD to
# have heard *them* recently is what separates an interruption from the room.
#
# A window rather than "right now" because a transcript arrives after the
# audio it describes: a caller who has just stopped talking would fail an
# instantaneous check and lose a real interruption. Two seconds is long
# enough to cover the transcriber's lag and short enough that a noise
# arriving well after the caller last spoke is not mistaken for them.
BARGE_IN_SPEECH_WINDOW_SECONDS = float(
    os.environ.get("BARGE_IN_SPEECH_WINDOW_SECONDS", "2.0")
)


# How many times the deafness watchdog may restart the speech-to-text stream,
# with nothing transcribed in between, before the assistant says out loud
# that it cannot hear.
#
# Distinct from MAX_STT_STREAM_RECONNECTS, which is 50 and exists for a
# provider that has genuinely gone away. A caller does not wait through 50
# restarts: on a real call the stream went deaf while the microphone was
# delivering healthy audio, was restarted twice, and the caller heard
# absolutely nothing at all for the whole session. Two restarts is about
# fifteen seconds of a person talking to a machine that is not listening,
# which is already too long to say nothing about.
STT_HEARING_TROUBLE_RESTARTS = int(
    os.environ.get("STT_HEARING_TROUBLE_RESTARTS", "2")
)

# How long before it may say so again. The notice is worth repeating if the
# trouble persists - a caller who hears it once and then nothing assumes the
# call is dead - but not on every restart.
STT_HEARING_TROUBLE_COOLDOWN_SECONDS = float(
    os.environ.get("STT_HEARING_TROUBLE_COOLDOWN_SECONDS", "25.0")
)


# Whether the transcriber is sent only the caller's own speech, with the room
# replaced by silence (app/speech_gate.py).
#
# On. A transcriber hears everything it is given and finds words in a
# television or a passing conversation far more readily than the detector
# calls that sound speech, and those words ended turns, cancelled replies and
# appeared in the transcript as things nobody said. Downstream guards stop
# them doing damage; this stops them being produced.
#
# Set false to send every frame again, which is what to do if the caller is
# being clipped and raising VAD_NOISE_MARGIN has not helped.
STT_GATE_ON_SPEECH = os.environ.get("STT_GATE_ON_SPEECH", "true").lower() == "true"

# How much audio from just before speech was confirmed is kept and sent with
# it. The detector is a little behind the caller, and without this the first
# syllable of every sentence is lost - which is the half of the requirement
# that is easy to forget while fixing the other half.
STT_GATE_PRE_ROLL_SECONDS = float(os.environ.get("STT_GATE_PRE_ROLL_SECONDS", "0.4"))

# How long speech keeps flowing after the detector goes quiet. Covers the
# pauses inside a sentence and the trailing word on a falling voice; too
# short and the caller's speech arrives as fragments, which is how one-word
# transcripts happen.
STT_GATE_HANGOVER_SECONDS = float(os.environ.get("STT_GATE_HANGOVER_SECONDS", "0.8"))
