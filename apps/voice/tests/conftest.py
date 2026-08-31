"""
Shared test helpers for the /media/session end-to-end pipeline tests -
used by both test_media_session.py and test_latency_regression.py, mirroring
apps/api/tests/conftest.py's own precedent for helpers reused across
multiple test files.
"""

import json
import uuid

import pytest
from norma_shared.mock_speech import MockTTS
from norma_shared.voice_session_ticket import create_voice_session_ticket
from pipecat.audio.vad.vad_analyzer import VADState

import app.main as main_module
import app.media_session as media_session_module
from app.llm_config_client import LLMConfig
from app.tts_config_client import TTSConfig
from app.turn_metrics import TurnMetricRecord

# Item 21a: a fixed test secret/algorithm, monkeypatched into app.main by
# _patch_session_setup below so every test's ticket, built with the same
# values, verifies without depending on a real SECRET_KEY being set in the
# test environment.
_TEST_SECRET_KEY = "test-secret-key-for-voice-session-tickets"
_TEST_JWT_ALGORITHM = "HS256"


def _test_ticket(assistant_id, *, ttl_seconds: float = 60) -> str:
    return create_voice_session_ticket(
        secret_key=_TEST_SECRET_KEY,
        algorithm=_TEST_JWT_ALGORITHM,
        assistant_id=str(assistant_id),
        ttl_seconds=ttl_seconds,
    )


def _media_session_url(assistant_id, *, ticket: str | None = None) -> str:
    """
    Builds a /media/session URL with a valid ticket for assistant_id, unless
    an explicit (possibly invalid) ticket is passed - for the rejection-path
    tests that need to connect with a bad or missing ticket.
    """

    if ticket is None:
        ticket = _test_ticket(assistant_id)

    return f"/media/session?ticket={ticket}"


class _ScriptedVADAnalyzer:
    """
    Returns one VADState per analyze_audio() call, in order (the last entry
    repeats once exhausted) - never loads the real Silero model, matching
    app/turn_detection.py's own test precedent.
    """

    def __init__(self, states: list[VADState]) -> None:
        self._states = states
        self._index = 0

    def set_sample_rate(self, sample_rate: int) -> None:
        pass

    async def analyze_audio(self, buffer: bytes) -> VADState:
        state = self._states[min(self._index, len(self._states) - 1)]
        self._index += 1

        return state


def _patch_turn_detector_vad(monkeypatch: pytest.MonkeyPatch, vad_analyzer) -> None:
    """
    Substitutes a scripted VAD analyzer into every TurnDetector this test
    creates, without touching the real pipeline construction otherwise -
    the real SileroVADAnalyzer must never load in the test suite.
    """

    real_turn_detector = media_session_module.TurnDetector

    def _fake_turn_detector(**kwargs):
        return real_turn_detector(**{**kwargs, "vad_analyzer": vad_analyzer})

    monkeypatch.setattr(media_session_module, "TurnDetector", _fake_turn_detector)


async def _fake_fetch_glossary_terms(assistant_id) -> list[str]:
    return ["tinnitus", "otoscopy"]


async def _fake_fetch_turn_sensitivity(assistant_id) -> float:
    return 0.5


async def _fake_fetch_llm_config(assistant_id) -> LLMConfig:
    return LLMConfig(system_prompt="You are a helpful assistant.", creativity=0.3)


async def _fake_fetch_retrieved_context(assistant_id, query) -> str:
    return ""


async def _fake_fetch_tts_config(assistant_id) -> TTSConfig:
    return TTSConfig(voice_id="voice-1", speech_rate=1.0)


async def _noop_record_turn_metric(assistant_id, record, *, client=None) -> None:
    """
    The default turn-metrics stand-in - every test that opens /media/session
    now finishes at least one turn, which fires a fire-and-forget POST via
    app.turn_metrics_client.record_turn_metric. Without this, that POST
    would attempt a real network call to a hostname ("api") that doesn't
    resolve outside Docker - not a fast failure, since DNS resolution for an
    unknown host can hang for many seconds before giving up, and Pipecat's
    own task-manager cleanup waits for every task it created (including
    this fire-and-forget one) to finish before a test's WebSocket context
    manager can exit. Mirrors _silent_tts's exact "safe default, tests that
    care override afterward" precedent.
    """


def _capturing_record_turn_metric(
    monkeypatch: pytest.MonkeyPatch,
) -> list[tuple[uuid.UUID, TurnMetricRecord]]:
    """
    For the tests that specifically need to inspect what would have been
    posted. Returns the list itself, appended to on every call, so a test
    can assert against it after driving the pipeline.
    """

    calls: list[tuple[uuid.UUID, TurnMetricRecord]] = []

    async def fake(assistant_id, record, *, client=None) -> None:
        calls.append((assistant_id, record))

    monkeypatch.setattr(media_session_module, "record_turn_metric", fake)

    return calls


def _silent_tts() -> MockTTS:
    """
    Produces zero audio for any text (bytes_per_character=0 means
    total_bytes is always 0, MockTTS.synthesize's own early return). The
    default TTS stand-in for every test that isn't specifically about TTS
    output - otherwise a background player task's real audio could race a
    test's fixed-count receive_text() calls, since neither the LLM's own
    deltas nor MockTTS's own synthesis have any inherent delay to keep
    them safely behind the text messages a test expects. Tests that do
    care about TTS output override get_tts_provider explicitly afterward.
    """

    return MockTTS(bytes_per_character=0)


def _patch_session_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Every test that opens /media/session triggers main.py's unconditional
    fetch_llm_config()/fetch_tts_config()/get_tts_provider() calls - mock
    them everywhere so no test ever attempts a real network call or
    produces racy background audio, matching every other session-setup
    fetch's existing precedent.
    """

    monkeypatch.setattr(main_module, "fetch_llm_config", _fake_fetch_llm_config)
    monkeypatch.setattr(main_module, "fetch_tts_config", _fake_fetch_tts_config)
    monkeypatch.setattr(main_module, "get_tts_provider", _silent_tts)
    monkeypatch.setattr(media_session_module, "record_turn_metric", _noop_record_turn_metric)
    monkeypatch.setattr(main_module, "SECRET_KEY", _TEST_SECRET_KEY)
    monkeypatch.setattr(main_module, "JWT_ALGORITHM", _TEST_JWT_ALGORITHM)


def _receive_one(ws) -> tuple[str, object]:
    """
    Reads one raw WebSocket message and returns ("bytes", raw_bytes) or
    ("text", parsed_json) - the generic Starlette receive(), not
    receive_text()/receive_bytes(), since this feature's messages mix JSON
    control messages with binary synthesized audio and exact interleaving
    between them is not something to assume (verified empirically while
    designing this feature: receive_text()/receive_bytes() assert their
    expected type and fail loudly on a mismatch rather than skipping ahead).
    """

    message = ws.receive()

    if "bytes" in message:
        return ("bytes", message["bytes"])

    return ("text", json.loads(message["text"]))
