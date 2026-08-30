import json

import pytest
from fastapi.testclient import TestClient
from norma_shared.mock_speech import MockSTT
from norma_shared.speech import TranscriptEvent
from pipecat.audio.vad.vad_analyzer import VADState

import app.main as main_module
import app.media_session as media_session_module
from app.main import app


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


def test_media_session_streams_partial_then_final_transcripts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Sends audio into /media/session and asserts a partial transcript
    arrives before all audio has been sent, followed by a final one -
    proving real streaming (not drain-then-yield) through the actual
    Pipecat pipeline (app/media_session.py), not a hand-rolled bypass.
    """

    partial = TranscriptEvent(text="hello", is_final=False)
    final = TranscriptEvent(text="hello there", is_final=True)
    mock_stt = MockSTT(script=[partial, final], chunks_before_event=[1, 4])

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity)
    _patch_turn_detector_vad(monkeypatch, _ScriptedVADAnalyzer([VADState.QUIET]))

    assistant_id = "00000000-0000-0000-0000-000000000001"

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/media/session?assistant_id={assistant_id}") as ws,
    ):
        # Large enough to clear the output/input chunking machinery, split
        # across several sends so MockSTT's chunks_before_event has audio
        # chunks to count against.
        chunk = bytes(range(256)) * 5
        for _ in range(4):
            ws.send_bytes(chunk)

        first = json.loads(ws.receive_text())
        second = json.loads(ws.receive_text())

    assert first == {"type": "transcript", "text": "hello", "is_final": False}
    assert second == {"type": "transcript", "text": "hello there", "is_final": True}


def test_media_session_passes_glossary_terms_to_the_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # chunks_before_event=[1] (interleaved mode), not the default
    # drain-then-yield: a live connection's audio stream never ends on its
    # own, so drain-then-yield would wait forever for a StopAsyncIteration
    # that never comes while the socket stays open.
    mock_stt = MockSTT(
        script=[TranscriptEvent(text="hi", is_final=True)], chunks_before_event=[1]
    )

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity)
    _patch_turn_detector_vad(monkeypatch, _ScriptedVADAnalyzer([VADState.QUIET]))

    assistant_id = "00000000-0000-0000-0000-000000000002"

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/media/session?assistant_id={assistant_id}") as ws,
    ):
        ws.send_bytes(bytes(range(256)) * 20)
        ws.receive_text()

    assert mock_stt.received_keywords == ["tinnitus", "otoscopy"]


def test_media_session_emits_turn_ended_after_silence_follows_a_final_transcript(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Proves item 20c's turn detection through the actual pipeline: a final
    transcript arrives, VAD reports sustained silence after having spoken,
    and a turn_ended message follows - using a scripted VAD analyzer, never
    the real Silero model.
    """

    final = TranscriptEvent(text="Book me in for Tuesday.", is_final=True)
    mock_stt = MockSTT(script=[final], chunks_before_event=[1])

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity)
    _patch_turn_detector_vad(
        monkeypatch,
        _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET, VADState.QUIET]),
    )

    assistant_id = "00000000-0000-0000-0000-000000000003"

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/media/session?assistant_id={assistant_id}") as ws,
    ):
        chunk = bytes(range(256)) * 5
        for _ in range(3):
            ws.send_bytes(chunk)

        messages = [json.loads(ws.receive_text()) for _ in range(2)]

    transcript_messages = [m for m in messages if m["type"] == "transcript"]
    turn_ended_messages = [m for m in messages if m["type"] == "turn_ended"]

    assert transcript_messages == [
        {"type": "transcript", "text": "Book me in for Tuesday.", "is_final": True}
    ]
    assert turn_ended_messages == [
        {"type": "turn_ended", "text": "Book me in for Tuesday."}
    ]
