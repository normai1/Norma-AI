"""
Item 20f's own "p95 budget enforced in CI" claim - see current-feature.md's
Architecture decisions for exactly what that means today: this proves the
instrumentation and the percentile computation are correct and would fail
the build on a real regression, under controlled mock-provider timing - not
that real production latency is within budget (item 62's job, against real
production topology).
"""

import uuid

import pytest
from fastapi.testclient import TestClient
from norma_shared.latency import percentile
from norma_shared.mock_speech import MockSTT, MockTTS
from norma_shared.speech import TranscriptEvent
from pipecat.audio.vad.vad_analyzer import VADState

import app.main as main_module
import app.media_session as media_session_module
from app.main import app
from app.mock_llm import MockLLM
from tests.conftest import (
    _capturing_record_turn_metric,
    _fake_fetch_glossary_terms,
    _fake_fetch_retrieved_context,
    _fake_fetch_turn_sensitivity,
    _media_session_url,
    _patch_session_setup,
    _patch_turn_detector_vad,
    _receive_one,
    _ScriptedVADAnalyzer,
)

# CLAUDE.md section 1's non-negotiable numbers.
_P50_BUDGET_MS = 700.0
_P95_BUDGET_MS = 1200.0

_TURN_COUNT = 20


def test_time_to_first_audio_stays_within_budget_across_many_turns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    transcripts = [
        TranscriptEvent(text=f"Question number {i}.", is_final=True)
        for i in range(_TURN_COUNT)
    ]
    # 3 audio chunks per turn, cumulative: [3, 6, 9, ...].
    chunks_before_event = [3 * (i + 1) for i in range(_TURN_COUNT)]
    mock_stt = MockSTT(script=transcripts, chunks_before_event=chunks_before_event)
    # Small, deliberately realistic (non-zero) delays - see this feature's
    # Architecture decisions for why this proves the plumbing works, not
    # real production latency.
    mock_llm = MockLLM(response="Sure, one moment.", chunk_delay_seconds=0.05)
    mock_tts = MockTTS(time_to_first_byte_seconds=0.1)

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "get_llm_provider", lambda: mock_llm)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity)
    _patch_session_setup(monkeypatch)
    monkeypatch.setattr(main_module, "get_tts_provider", lambda: mock_tts)
    monkeypatch.setattr(
        media_session_module, "fetch_retrieved_context", _fake_fetch_retrieved_context
    )
    _patch_turn_detector_vad(
        monkeypatch,
        _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET, VADState.QUIET] * _TURN_COUNT),
    )
    calls = _capturing_record_turn_metric(monkeypatch)

    assistant_id = uuid.uuid4()
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        for turn in range(_TURN_COUNT):
            for _ in range(3):
                ws.send_bytes(chunk)

            # Drain messages until this turn's own record has actually
            # been posted - calls (not any particular message type) is the
            # real gate, since it is what the assertions below read from.
            expected_calls = turn + 1

            while len(calls) < expected_calls:
                _receive_one(ws)

    assert len(calls) == _TURN_COUNT

    durations_ms = []

    for _, record in calls:
        assert record.stt_finalized_at is not None
        assert record.audio_out_at is not None
        durations_ms.append(_milliseconds(record.audio_out_at - record.stt_finalized_at))

    p50 = percentile(durations_ms, 0.5)
    p95 = percentile(durations_ms, 0.95)

    assert p50 is not None
    assert p95 is not None
    assert p50 < _P50_BUDGET_MS
    assert p95 < _P95_BUDGET_MS


def _milliseconds(delta) -> float:
    return delta.total_seconds() * 1000
