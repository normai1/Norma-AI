import json

import pytest
from fastapi.testclient import TestClient
from norma_shared.mock_speech import MockSTT, MockTTS
from norma_shared.speech import SpeechProviderUnavailable, TranscriptEvent
from pipecat.audio.vad.vad_analyzer import VADState

import app.main as main_module
import app.media_session as media_session_module
from app.llm import LLMProviderUnavailable
from app.llm_config_client import LLMConfig
from app.main import app
from app.mock_llm import MockLLM
from app.tts_config_client import TTSConfig


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
    _patch_session_setup(monkeypatch)
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
    _patch_session_setup(monkeypatch)
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
    _patch_session_setup(monkeypatch)
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

        # 5 messages now, not 2: item 20d's LLMTurnProcessor also reacts to
        # turn_ended and pushes its own llm_complete (empty text, since
        # MockLLM()'s default response is "") once the turn resolves, item
        # 20e's caller_speech_started fires once for the SPEAKING frame
        # that starts this turn, and TTSProcessor's reply_finished fires
        # once the (empty, silently-synthesized) reply is fully done.
        messages = [json.loads(ws.receive_text()) for _ in range(5)]

    transcript_messages = [m for m in messages if m["type"] == "transcript"]
    turn_ended_messages = [m for m in messages if m["type"] == "turn_ended"]
    llm_complete_messages = [m for m in messages if m["type"] == "llm_complete"]
    caller_speech_started_messages = [
        m for m in messages if m["type"] == "caller_speech_started"
    ]
    reply_finished_messages = [m for m in messages if m["type"] == "reply_finished"]

    assert transcript_messages == [
        {"type": "transcript", "text": "Book me in for Tuesday.", "is_final": True}
    ]
    assert turn_ended_messages == [
        {"type": "turn_ended", "text": "Book me in for Tuesday."}
    ]
    assert llm_complete_messages == [{"type": "llm_complete", "text": ""}]
    assert caller_speech_started_messages == [{"type": "caller_speech_started"}]
    assert reply_finished_messages == [{"type": "reply_finished"}]


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
    _patch_session_setup(monkeypatch)
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

        # 8, not 6: item 20e's caller_speech_started fires once for this
        # turn's SPEAKING frame, and reply_finished fires once TTSProcessor
        # (silenced via _patch_session_setup) has finished the reply.
        messages = [json.loads(ws.receive_text()) for _ in range(8)]

    transcript_messages = [m for m in messages if m["type"] == "transcript"]
    turn_ended_messages = [m for m in messages if m["type"] == "turn_ended"]
    llm_delta_messages = [m for m in messages if m["type"] == "llm_delta"]
    llm_complete_messages = [m for m in messages if m["type"] == "llm_complete"]
    caller_speech_started_messages = [
        m for m in messages if m["type"] == "caller_speech_started"
    ]
    reply_finished_messages = [m for m in messages if m["type"] == "reply_finished"]

    assert transcript_messages == [
        {"type": "transcript", "text": "What are your hours?", "is_final": True}
    ]
    assert turn_ended_messages == [{"type": "turn_ended", "text": "What are your hours?"}]
    assert "".join(m["text"] for m in llm_delta_messages) == "We are open nine to five."
    assert llm_complete_messages == [
        {"type": "llm_complete", "text": "We are open nine to five."}
    ]
    assert caller_speech_started_messages == [{"type": "caller_speech_started"}]
    assert reply_finished_messages == [{"type": "reply_finished"}]


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
    _patch_session_setup(monkeypatch)
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

        # 6, not 4: item 20e's caller_speech_started fires once for this
        # turn's SPEAKING frame, and reply_finished fires once TTSProcessor
        # discards the abandoned "Sure" fragment and resets (an error
        # reply is still "finished" from the turn-detection perspective).
        messages = [json.loads(ws.receive_text()) for _ in range(6)]

    types = [m["type"] for m in messages]

    assert types.count("caller_speech_started") == 1
    assert types.count("reply_finished") == 1
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
    _patch_session_setup(monkeypatch)
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

        # 6, not 4: item 20e's caller_speech_started fires once for each
        # turn's own SPEAKING onset (there is a QUIET gap between the two
        # turns, so each gets its own edge), and TTSProcessor's
        # reply_finished is the only observable proof that
        # reset_for_next_turn() actually ran before turn 2's audio is
        # sent - without waiting for it, sending turn 2 right after
        # llm_complete would race the still-latched detector, since the
        # reset now happens in TTSProcessor's own background task instead
        # of synchronously alongside a message already being read.
        first_turn_messages = [json.loads(ws.receive_text()) for _ in range(6)]

        for _ in range(3):
            ws.send_bytes(chunk)

        second_turn_messages = [json.loads(ws.receive_text()) for _ in range(6)]

    def _types(messages: list[dict]) -> list[str]:
        return sorted(m["type"] for m in messages)

    expected_types = [
        "caller_speech_started",
        "llm_complete",
        "llm_delta",
        "reply_finished",
        "transcript",
        "turn_ended",
    ]
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


def test_media_session_emits_caller_speech_started_on_a_genuine_onset_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Item 20e's barge-in signal: fires once per speech onset, not once per
    audio frame while already speaking - proven by sending two SPEAKING
    frames back to back (only the first should fire) separated by a QUIET
    frame from a second SPEAKING frame (which should fire again).
    """

    mock_stt = MockSTT()

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity)
    _patch_session_setup(monkeypatch)
    _patch_turn_detector_vad(
        monkeypatch,
        _ScriptedVADAnalyzer(
            [VADState.SPEAKING, VADState.SPEAKING, VADState.QUIET, VADState.SPEAKING]
        ),
    )

    assistant_id = "00000000-0000-0000-0000-000000000008"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/media/session?assistant_id={assistant_id}") as ws,
    ):
        for _ in range(4):
            ws.send_bytes(chunk)

        messages = [json.loads(ws.receive_text()) for _ in range(2)]

    assert messages == [
        {"type": "caller_speech_started"},
        {"type": "caller_speech_started"},
    ]


def test_media_session_cancels_an_in_flight_llm_call_on_caller_speech_started(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Proves barge-in cancels a still-running LLM call (no llm_complete or
    llm_error ever arrives for it - proven by construction, since the next
    receive_text() call would fail on a content/type mismatch if either had
    snuck in), and that the detector still correctly resets afterward - a
    second, genuinely new turn is detected and answered on its own.
    """

    first_final = TranscriptEvent(text="First question.", is_final=True)
    second_final = TranscriptEvent(text="Second question.", is_final=True)
    mock_stt = MockSTT(script=[first_final, second_final], chunks_before_event=[1, 5])
    mock_llm = MockLLM(response="Sure thing.", chunk_delay_seconds=0.2)

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "get_llm_provider", lambda: mock_llm)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity)
    _patch_session_setup(monkeypatch)
    monkeypatch.setattr(
        media_session_module, "fetch_retrieved_context", _fake_fetch_retrieved_context
    )
    _patch_turn_detector_vad(
        monkeypatch,
        _ScriptedVADAnalyzer(
            [
                VADState.SPEAKING,
                VADState.QUIET,
                VADState.SPEAKING,
                VADState.SPEAKING,
                VADState.QUIET,
            ]
        ),
    )

    assistant_id = "00000000-0000-0000-0000-000000000009"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/media/session?assistant_id={assistant_id}") as ws,
    ):
        ws.send_bytes(chunk)
        ws.send_bytes(chunk)

        # The very first SPEAKING frame of the whole session also fires
        # its own onset caller_speech_started, ahead of turn 1's transcript.
        caller_speech_started_at_session_start = json.loads(ws.receive_text())
        transcript_1 = json.loads(ws.receive_text())
        turn_ended_1 = json.loads(ws.receive_text())

        # Turn 1's LLM call is now in flight (MockLLM is sleeping
        # chunk_delay_seconds before its first delta) - interrupt it.
        # Also produces reply_finished (turn_ended() is still latched True
        # from turn 1's own reset not having run yet, so TTSProcessor's
        # barge-in guard treats this as a real interruption to announce).
        ws.send_bytes(chunk)
        barge_in_messages = [json.loads(ws.receive_text()) for _ in range(2)]

        ws.send_bytes(chunk)
        ws.send_bytes(chunk)

        transcript_2 = json.loads(ws.receive_text())
        turn_ended_2 = json.loads(ws.receive_text())

    assert caller_speech_started_at_session_start == {"type": "caller_speech_started"}
    assert transcript_1 == {"type": "transcript", "text": "First question.", "is_final": True}
    assert turn_ended_1 == {"type": "turn_ended", "text": "First question."}
    assert sorted(m["type"] for m in barge_in_messages) == [
        "caller_speech_started",
        "reply_finished",
    ]
    assert transcript_2 == {"type": "transcript", "text": "Second question.", "is_final": True}
    assert turn_ended_2 == {"type": "turn_ended", "text": "Second question."}


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


def test_media_session_streams_sentence_audio_before_the_llm_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The headline claim of item 20e: a complete sentence is synthesized and
    played as soon as it is ready, not once the whole LLM reply has
    finished streaming. chunk_delay_seconds creates a real window between
    the two LLM deltas; the first sentence's audio should arrive well
    within it, before the second delta or llm_complete.
    """

    final = TranscriptEvent(text="What are your hours?", is_final=True)
    mock_stt = MockSTT(script=[final], chunks_before_event=[1])
    mock_llm = MockLLM(
        response="We open at nine. We close at five.", chunk_words=4, chunk_delay_seconds=0.3
    )
    mock_tts = MockTTS()

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
        _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET, VADState.QUIET]),
    )

    assistant_id = "00000000-0000-0000-0000-00000000000a"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/media/session?assistant_id={assistant_id}") as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        received = []
        expected_audio_length = (len("We open at nine.") + len("We close at five.")) * 320

        # Deliberately not terminated on reply_finished: it is pushed as an
        # OutputTransportMessageUrgentFrame, which Pipecat's output
        # transport can deliver to the wire ahead of already-pushed-but-
        # not-yet-flushed OutputAudioRawFrame bytes - confirmed empirically
        # (the second sentence's entire audio arrived only after
        # reply_finished, not before it). Draining until every expected
        # audio byte has actually arrived is the only reliable stop
        # condition; Pipecat's own idle-timeout is the safety net if a real
        # regression means it never does.
        while (
            len(b"".join(value for kind, value in received if kind == "bytes"))
            < expected_audio_length
        ):
            entry = _receive_one(ws)
            received.append(entry)

    kinds = [kind for kind, _ in received]
    first_audio_index = kinds.index("bytes")
    llm_complete_index = next(
        i
        for i, (kind, value) in enumerate(received)
        if kind == "text" and value["type"] == "llm_complete"
    )

    assert first_audio_index < llm_complete_index

    audio_bytes = b"".join(value for kind, value in received if kind == "bytes")
    # Not an exact match: Pipecat's output transport pads each sentence's
    # own trailing sub-chunk remainder up to a full audio_chunk_size frame
    # with silence before flushing it (TTSProcessor pushes a TTSStoppedFrame
    # per sentence specifically so that remainder isn't dropped - see its
    # docstring). That padding is a transport implementation detail this
    # test has no business pinning an exact byte count to; what actually
    # matters is that every synthesized byte survived the round trip.
    assert len(audio_bytes) >= expected_audio_length

    text_messages = [value for kind, value in received if kind == "text"]
    assert {
        "type": "transcript",
        "text": "What are your hours?",
        "is_final": True,
    } in text_messages
    assert {"type": "turn_ended", "text": "What are your hours?"} in text_messages
    assert {
        "type": "llm_complete",
        "text": "We open at nine. We close at five.",
    } in text_messages


def test_media_session_emits_tts_error_when_the_provider_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    CLAUDE.md's "provider failure never produces silence" applied to the
    TTS stage: MockTTS raises before yielding any audio, and exactly one
    tts_error message arrives instead - no crash, no hang, no audio.
    """

    final = TranscriptEvent(text="Can you help me?", is_final=True)
    mock_stt = MockSTT(script=[final], chunks_before_event=[1])
    mock_llm = MockLLM(response="Sure thing.")
    mock_tts = MockTTS(failure=SpeechProviderUnavailable("boom"))

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
        _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET, VADState.QUIET]),
    )

    assistant_id = "00000000-0000-0000-0000-00000000000b"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/media/session?assistant_id={assistant_id}") as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        received = []

        while True:
            entry = _receive_one(ws)
            received.append(entry)

            if entry == ("text", {"type": "reply_finished"}):
                break

    assert all(kind == "text" for kind, _ in received)

    text_messages = [value for _, value in received]
    assert {
        "type": "tts_error",
        "text": "Sorry, I'm having trouble speaking right now.",
    } in text_messages


def test_media_session_barge_in_stops_pending_tts_before_any_audio_and_starts_a_new_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The other half of item 20e's headline claim: caller speech cancels
    playback immediately. MockTTS's time_to_first_byte_seconds guarantees
    zero audio bytes are ever emitted for the first reply before the
    interruption arrives; the interrupting speech is then itself detected
    and answered as a genuinely new turn.
    """

    first_final = TranscriptEvent(text="First question.", is_final=True)
    second_final = TranscriptEvent(text="Second question.", is_final=True)
    mock_stt = MockSTT(script=[first_final, second_final], chunks_before_event=[1, 4])
    mock_llm = MockLLM(response="Sure thing.")
    mock_tts = MockTTS(time_to_first_byte_seconds=1.0)

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

    assistant_id = "00000000-0000-0000-0000-00000000000c"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(f"/media/session?assistant_id={assistant_id}") as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        # Read until turn 1's llm_complete - by then the LLM has replied,
        # and MockTTS is still asleep inside time_to_first_byte_seconds,
        # so no audio can have arrived yet.
        first_turn_messages = []

        while True:
            entry = _receive_one(ws)
            first_turn_messages.append(entry)

            if entry[0] == "text" and entry[1]["type"] == "llm_complete":
                break

        assert all(kind == "text" for kind, _ in first_turn_messages)

        # Interrupt before any audio for turn 1 was ever sent.
        for _ in range(3):
            ws.send_bytes(chunk)

        second_turn_messages = []

        while True:
            entry = _receive_one(ws)
            second_turn_messages.append(entry)

            if entry[0] == "text" and entry[1]["type"] == "turn_ended":
                break

    first_turn_text = [value for kind, value in first_turn_messages if kind == "text"]
    assert {
        "type": "transcript",
        "text": "First question.",
        "is_final": True,
    } in first_turn_text

    second_turn_text = [value for kind, value in second_turn_messages if kind == "text"]
    assert {"type": "caller_speech_started"} in second_turn_text
    assert {
        "type": "transcript",
        "text": "Second question.",
        "is_final": True,
    } in second_turn_text
    assert {"type": "turn_ended", "text": "Second question."} in second_turn_text
    assert all(kind == "text" for kind, _ in second_turn_messages)
