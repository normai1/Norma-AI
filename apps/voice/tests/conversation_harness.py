"""
Reusable fixture-audio conversation replay harness (build-plan item 22).

Every test in test_media_session.py hand-wires the same provider
monkeypatching, VAD scripting, and WebSocket setup inline - each one a
hard-won, individually-debugged proof of one pipeline behavior (several
caught real bugs during items 20e-20g), and deliberately left as-is rather
than retrofitted onto this harness (see this feature's spec). This module
is the next layer up: a small, reusable entry point for *new* conversation
tests, built directly on top of conftest.py's existing pieces rather than
duplicating them.

Where the rest of the pipeline's behavioral coverage already lives:
  - test_media_session.py    - the full end-to-end proof, 25 tests: partial/
                                final transcripts, turn detection, LLM
                                streaming, barge-in, TTS, provider retry and
                                failover, turn metrics, ticket rejection.
  - test_turn_detection.py   - TurnDetector in isolation, 16 tests.
  - test_latency_regression.py - the p50/p95 time-to-first-audio budget.
  - test_session_resilience.py - SessionResilienceTracker in isolation.

Tests using this harness (test_conversation_replay.py) add the two
dimensions that were still genuinely missing: a real multi-turn
conversation with a mid-reply interruption, and an end-to-end proof of the
turn-detector's fallback timeout.
"""

import uuid
from collections.abc import Collection, Sequence
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from norma_shared.mock_speech import MockSTT, MockTTS
from pipecat.audio.vad.vad_analyzer import VADState

import app.main as main_module
import app.media_session as media_session_module
from app.main import app as fastapi_app
from app.mock_llm import MockLLM
from tests.conftest import (
    _fake_fetch_retrieved_context,
    _media_session_url,
    _patch_session_setup,
    _patch_turn_detector_vad,
    _receive_one,
    _ScriptedVADAnalyzer,
)

_DEFAULT_GLOSSARY_TERMS = ("tinnitus", "otoscopy")
_DEFAULT_TURN_SENSITIVITY = 0.5
_DEFAULT_AUDIO_CHUNK = bytes(range(256)) * 5


@contextmanager
def open_conversation_session(
    monkeypatch: pytest.MonkeyPatch,
    *,
    mock_stt: MockSTT | None = None,
    mock_llm: MockLLM | None = None,
    mock_tts: MockTTS | None = None,
    vad_states: Sequence[VADState] = (VADState.QUIET,),
    clock=None,
    assistant_id: str | None = None,
    glossary_terms: Sequence[str] = _DEFAULT_GLOSSARY_TERMS,
    turn_sensitivity: float = _DEFAULT_TURN_SENSITIVITY,
):
    """
    Wires providers and VAD scripting for one conversation scenario, then
    opens the real /media/session WebSocket route (via a valid test
    ticket) exactly as every hand-wired test already does - just collected
    in one place. Yields the open WebSocket test session.

    clock optionally injects a fake clock into every TurnDetector the
    session creates, for testing FALLBACK_TIMEOUT_SECONDS deterministically
    (see conftest.py's _patch_turn_detector_vad).
    """

    mock_stt = mock_stt if mock_stt is not None else MockSTT()
    mock_llm = mock_llm if mock_llm is not None else MockLLM()

    async def _fetch_glossary_terms(_assistant_id: uuid.UUID) -> list[str]:
        return list(glossary_terms)

    async def _fetch_turn_sensitivity(_assistant_id: uuid.UUID) -> float:
        return turn_sensitivity

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "get_llm_provider", lambda: mock_llm)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fetch_turn_sensitivity)
    _patch_session_setup(monkeypatch)

    if mock_tts is not None:
        monkeypatch.setattr(main_module, "get_tts_provider", lambda: mock_tts)

    monkeypatch.setattr(
        media_session_module, "fetch_retrieved_context", _fake_fetch_retrieved_context
    )
    _patch_turn_detector_vad(
        monkeypatch, _ScriptedVADAnalyzer(list(vad_states)), clock=clock
    )

    resolved_assistant_id = assistant_id or str(uuid.uuid4())

    with (
        TestClient(fastapi_app) as client,
        client.websocket_connect(_media_session_url(resolved_assistant_id)) as ws,
    ):
        yield ws


def send_audio_chunks(ws, count: int, *, chunk: bytes | None = None) -> None:
    """Sends `count` copies of `chunk` (or a default filler chunk) as binary WebSocket frames."""

    chunk = chunk if chunk is not None else _DEFAULT_AUDIO_CHUNK

    for _ in range(count):
        ws.send_bytes(chunk)


def receive_until(
    ws,
    *,
    stop_types: Collection[str],
    limit: int = 50,
) -> list[tuple[str, object]]:
    """
    Collects every message (via conftest.py's _receive_one) until one whose
    JSON "type" is in stop_types arrives (inclusive), or `limit` messages
    have been read. Raises AssertionError with the partial trace if the
    limit is hit first, so a hung or misconfigured fixture fails the test
    immediately instead of hanging the suite.
    """

    trace: list[tuple[str, object]] = []

    for _ in range(limit):
        kind, payload = _receive_one(ws)
        trace.append((kind, payload))

        if kind == "text" and payload.get("type") in stop_types:
            return trace

    raise AssertionError(
        f"receive_until: read {limit} messages without seeing any of {stop_types!r}; "
        f"trace so far: {trace}"
    )
