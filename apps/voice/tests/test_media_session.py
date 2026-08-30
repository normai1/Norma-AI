import json

import pytest
from fastapi.testclient import TestClient
from norma_shared.mock_speech import MockSTT
from norma_shared.speech import TranscriptEvent
from pipecat.audio.vad.vad_analyzer import VADState

import app.main as main_module
import app.media_session as media_session_module
from app.llm import LLMProviderUnavailable
from app.llm_config_client import LLMConfig
from app.main import app
from app.mock_llm import MockLLM


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


def _patch_llm_session_setup(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Every test that opens /media/session now triggers main.py's
    unconditional fetch_llm_config() call - mock it everywhere so no test
    ever attempts a real network call, matching every other session-setup
    fetch's existing precedent.
    """

    monkeypatch.setattr(main_module, "fetch_llm_config", _fake_fetch_llm_config)


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
    _patch_llm_session_setup(monkeypatch)
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
    _patch_llm_session_setup(monkeypatch)
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
    monkeypatch.setattr(main_module, "get_llm_provider", lambda: MockLLM())
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity)
    monkeypatch.setattr(main_module, "fetch_llm_config", _fake_fetch_llm_config)
    monkeypatch.setattr(
        media_session_module, "fetch_retrieved_context", _fake_fetch_retrieved_context
    )
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

        # 3 messages now, not 2: item 20d's LLMTurnProcessor also reacts to
        # turn_ended and pushes its own llm_complete (empty text, since
        # MockLLM()'s default response is "") once the turn resolves.
        messages = [json.loads(ws.receive_text()) for _ in range(3)]

    transcript_messages = [m for m in messages if m["type"] == "transcript"]
    turn_ended_messages = [m for m in messages if m["type"] == "turn_ended"]
    llm_complete_messages = [m for m in messages if m["type"] == "llm_complete"]

    assert transcript_messages == [
        {"type": "transcript", "text": "Book me in for Tuesday.", "is_final": True}
    ]
    assert turn_ended_messages == [
        {"type": "turn_ended", "text": "Book me in for Tuesday."}
    ]
    assert llm_complete_messages == [{"type": "llm_complete", "text": ""}]


def test_media_session_streams_an_llm_reply_after_a_turn_ends(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Proves item 20d's turn loop end to end: a completed turn produces a
    streamed LLM reply (llm_delta chunks, then llm_complete) built from the
    provider's scripted response - using MockLLM, never a real model.
    """

    final = TranscriptEvent(text="What are your hours?", is_final=True)
    mock_stt = MockSTT(script=[final], chunks_before_event=[1])
    mock_llm = MockLLM(response="We are open nine to five.", chunk_words=2)

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "get_llm_provider", lambda: mock_llm)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity)
    monkeypatch.setattr(main_module, "fetch_llm_config", _fake_fetch_llm_config)
    monkeypatch.setattr(
        media_session_module, "fetch_retrieved_context", _fake_fetch_retrieved_context
    )
    _patch_turn_detector_vad(
        monkeypatch,
        _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET, VADState.QUIET]),
    )

    assistant_id = "00000000-0000-0000-0000-000000000004"

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/media/session?assistant_id={assistant_id}") as ws,
    ):
        chunk = bytes(range(256)) * 5
        for _ in range(3):
            ws.send_bytes(chunk)

        messages = [json.loads(ws.receive_text()) for _ in range(6)]

    transcript_messages = [m for m in messages if m["type"] == "transcript"]
    turn_ended_messages = [m for m in messages if m["type"] == "turn_ended"]
    llm_delta_messages = [m for m in messages if m["type"] == "llm_delta"]
    llm_complete_messages = [m for m in messages if m["type"] == "llm_complete"]

    assert transcript_messages == [
        {"type": "transcript", "text": "What are your hours?", "is_final": True}
    ]
    assert turn_ended_messages == [{"type": "turn_ended", "text": "What are your hours?"}]
    assert "".join(m["text"] for m in llm_delta_messages) == "We are open nine to five."
    assert llm_complete_messages == [
        {"type": "llm_complete", "text": "We are open nine to five."}
    ]


def test_media_session_emits_llm_error_when_the_provider_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    CLAUDE.md's "provider failure never produces silence" applied to the
    LLM turn loop: MockLLM raises mid-stream, and exactly one llm_error
    message arrives - no crash, no hang, no llm_complete.
    """

    final = TranscriptEvent(text="Can you help me?", is_final=True)
    mock_stt = MockSTT(script=[final], chunks_before_event=[1])
    mock_llm = MockLLM(response="Sure", failure=LLMProviderUnavailable("boom"))

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "get_llm_provider", lambda: mock_llm)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity)
    monkeypatch.setattr(main_module, "fetch_llm_config", _fake_fetch_llm_config)
    monkeypatch.setattr(
        media_session_module, "fetch_retrieved_context", _fake_fetch_retrieved_context
    )
    _patch_turn_detector_vad(
        monkeypatch,
        _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET, VADState.QUIET]),
    )

    assistant_id = "00000000-0000-0000-0000-000000000006"

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/media/session?assistant_id={assistant_id}") as ws,
    ):
        chunk = bytes(range(256)) * 5
        for _ in range(3):
            ws.send_bytes(chunk)

        messages = [json.loads(ws.receive_text()) for _ in range(4)]

    types = [m["type"] for m in messages]

    assert types.count("llm_error") == 1
    assert types.count("llm_complete") == 0

    error_message = next(m for m in messages if m["type"] == "llm_error")
    assert error_message["text"] == "Sorry, I'm having trouble responding right now."


def test_media_session_detects_and_answers_a_second_independent_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The real, reachable proof of "conversation state" (see this feature's
    spec Architecture decisions for why an "overlapping in-flight" test is
    not the right one to write instead): after the first turn's reply
    fully completes, a fresh speak-silence-complete-transcript cycle is
    detected and answered on its own. Sends and reads in two stages so the
    first turn's reset_for_next_turn() has definitely already run - via
    that turn's llm_complete having been received - before the second
    turn's audio is sent, avoiding a race with the still-latched detector.
    """

    first_final = TranscriptEvent(text="First question.", is_final=True)
    second_final = TranscriptEvent(text="Second question.", is_final=True)
    mock_stt = MockSTT(script=[first_final, second_final], chunks_before_event=[1, 4])
    mock_llm = MockLLM(response="Sure thing.", chunk_words=5)

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "get_llm_provider", lambda: mock_llm)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity)
    monkeypatch.setattr(main_module, "fetch_llm_config", _fake_fetch_llm_config)
    monkeypatch.setattr(
        media_session_module, "fetch_retrieved_context", _fake_fetch_retrieved_context
    )
    _patch_turn_detector_vad(
        monkeypatch,
        _ScriptedVADAnalyzer(
            [
                VADState.SPEAKING,
                VADState.QUIET,
                VADState.QUIET,
                VADState.SPEAKING,
                VADState.QUIET,
                VADState.QUIET,
            ]
        ),
    )

    assistant_id = "00000000-0000-0000-0000-000000000007"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/media/session?assistant_id={assistant_id}") as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        first_turn_messages = [json.loads(ws.receive_text()) for _ in range(4)]

        for _ in range(3):
            ws.send_bytes(chunk)

        second_turn_messages = [json.loads(ws.receive_text()) for _ in range(4)]

    def _types(messages: list[dict]) -> list[str]:
        return sorted(m["type"] for m in messages)

    expected_types = ["llm_complete", "llm_delta", "transcript", "turn_ended"]
    assert _types(first_turn_messages) == expected_types
    assert _types(second_turn_messages) == expected_types

    first_transcript = next(m for m in first_turn_messages if m["type"] == "transcript")
    second_transcript = next(m for m in second_turn_messages if m["type"] == "transcript")
    assert first_transcript["text"] == "First question."
    assert second_transcript["text"] == "Second question."

    first_turn_ended = next(m for m in first_turn_messages if m["type"] == "turn_ended")
    second_turn_ended = next(m for m in second_turn_messages if m["type"] == "turn_ended")
    assert first_turn_ended["text"] == "First question."
    assert second_turn_ended["text"] == "Second question."
