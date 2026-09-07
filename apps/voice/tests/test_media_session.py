import json
import time
import uuid
from concurrent.futures import CancelledError as FutureCancelledError
from unittest.mock import MagicMock

import jwt
import pytest
from fastapi.testclient import TestClient
from norma_shared.mock_speech import MockSTT, MockTTS
from norma_shared.speech import SpeechProviderUnavailable, TranscriptEvent
from pipecat.audio.vad.vad_analyzer import VADState
from starlette.websockets import WebSocketDisconnect

import app.main as main_module
import app.media_session as media_session_module
from app import config
from app.llm import LLMProviderUnavailable
from app.main import app
from app.media_session import (
    _mostly_already_said,
    build_voice_session_pipeline_worker,
)
from app.mock_llm import MockLLM
from tests.conftest import (
    _TEST_JWT_ALGORITHM,
    _TEST_SECRET_KEY,
    _capturing_record_turn_metric,
    _fake_fetch_glossary_terms,
    _fake_fetch_retrieved_context,
    _fake_fetch_turn_sensitivity,
    _media_session_url,
    _patch_session_setup,
    _patch_turn_detector_vad,
    _receive_one,
    _ScriptedVADAnalyzer,
    _test_ticket,
)


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
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
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
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
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
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
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
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        chunk = bytes(range(256)) * 5
        for _ in range(3):
            ws.send_bytes(chunk)

        # Read to reply_finished rather than a fixed count: deltas are
        # emitted per sentence now that each one is checked before the
        # caller hears it (item 24b), so how many arrive depends on the
        # reply's punctuation, not on the provider's chunk size.
        messages = []

        while True:
            message = json.loads(ws.receive_text())
            messages.append(message)

            if message["type"] == "reply_finished":
                break

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
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        chunk = bytes(range(256)) * 5
        for _ in range(3):
            ws.send_bytes(chunk)

        # 6, not 4: item 20e's caller_speech_started fires once for this
        # turn's SPEAKING frame, and reply_finished fires once TTSProcessor
        # discards the abandoned "Sure" fragment and resets (an error
        # reply is still "finished" from the turn-detection perspective).
        # Read to the terminal message rather than a fixed count: deltas are
        # per sentence now (item 24b), so a reply that never completes one
        # produces none at all.
        messages = []

        while True:
            message = json.loads(ws.receive_text())
            messages.append(message)

            # llm_error is what this test is about, and it can arrive after
            # reply_finished now that a reply with no completed sentence
            # emits no deltas at all.
            if message["type"] == "llm_error":
                break

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
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
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
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
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
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
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
        # No playback_cancelled here: not one audio frame has reached the
        # output transport yet, so there is nothing buffered for the caller
        # to still be hearing and nothing to flush. reply_finished still
        # follows (turn_ended() is latched True from turn 1's own reset not
        # having run yet, so the barge-in is a real interruption to
        # announce).
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
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
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


def test_media_session_prefetches_the_next_sentences_tts_while_the_current_one_plays(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The bug fix behind "the assistant sounds like it's reading a script":
    the second sentence's TTS first-byte round-trip must overlap the first
    sentence's own playback, not start only once it finishes. Proven via
    MockTTS.call_started_at: the pre-fix behavior could not start call 2
    until call 1's own time_to_first_byte_seconds wait (plus its streaming)
    had fully elapsed; overlapped, call 2 starts within a small scheduling
    delta of call 1 - regardless of unrelated fixed overhead elsewhere in
    the pipeline (turn detection, worker startup), which this comparison
    of the two calls' own relative timing is immune to.
    """

    final = TranscriptEvent(text="What are your hours?", is_final=True)
    mock_stt = MockSTT(script=[final], chunks_before_event=[1])
    mock_llm = MockLLM(response="We open at nine. We close at five.")
    mock_tts = MockTTS(time_to_first_byte_seconds=0.2)

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

    assistant_id = "00000000-0000-0000-0000-000000000016"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        received = []
        expected_audio_length = (len("We open at nine.") + len("We close at five.")) * 320

        while (
            len(b"".join(value for kind, value in received if kind == "bytes"))
            < expected_audio_length
        ):
            entry = _receive_one(ws)
            received.append(entry)

    assert mock_tts.call_count == 2
    call_gap = mock_tts.call_started_at[1] - mock_tts.call_started_at[0]
    # Sequential (pre-fix) could not start call 2 until call 1's own 0.2s
    # first-byte wait had elapsed; overlapped, call 2 starts within a
    # small scheduling delta of call 1 - well under half that delay even
    # with real test-environment jitter.
    assert call_gap < 0.1


def test_media_session_reconnects_when_stt_closes_without_transcribing_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Reported as "the assistant is not responding anything", and measured
    against the live provider: ElevenLabs' realtime STT socket sometimes
    closes a second or two into a session, cleanly, having yielded nothing
    and raised nothing. That is invisible to any retry keyed on exceptions,
    and the original code treated it as a stream that had finished its work
    - so the caller was never transcribed again for the whole call, with no
    transcript, no error and no failover to show for it. The session must
    reconnect and go on to answer normally.
    """

    final = TranscriptEvent(text="What are your hours?", is_final=True)
    mock_stt = MockSTT(script=[final], chunks_before_event=[1], silent_closes=1)
    mock_llm = MockLLM(response="We open at nine.")
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

    assistant_id = "00000000-0000-0000-0000-00000000001a"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        messages = []

        while True:
            kind, value = _receive_one(ws)

            if kind == "text":
                messages.append(value)

                if value == {"type": "reply_finished"}:
                    break

    assert mock_stt.call_count == 2, "the silent close should have been reconnected"
    assert {"type": "turn_ended", "text": "What are your hours?"} in messages
    assert not any(message.get("type") == "session_failover" for message in messages)


def test_media_session_reconnects_when_stt_closes_after_working_normally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Reported as "the assistant stops answering after two responses", and
    found in a real session's logs: the STT stream transcribed 14 events
    over four healthy minutes, closed, and the remaining 56 seconds of that
    call reached nothing at all.

    A stream that has done useful work and then closes is the *common* case
    of a live provider ending its own stream, not a sign the call is over -
    only our own end-of-input means that. An earlier fix here reconnected
    solely on closes that had produced no transcripts, which left exactly
    this case unhandled. close_after_script=True models the provider hanging
    up on its own after delivering its script.
    """

    mock_stt = MockSTT(
        script=[TranscriptEvent(text="What are your hours?", is_final=True)],
        chunks_before_event=[1],
        close_after_script=True,
    )
    mock_llm = MockLLM(response="We open at nine.")
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
    # The real delay only exists to keep a refusing provider from becoming a
    # hot loop; waiting it out here would just make the test slow.
    monkeypatch.setattr(config, "STT_RECONNECT_DELAY_SECONDS", 0.01)

    assistant_id = "00000000-0000-0000-0000-00000000001b"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        while True:
            kind, value = _receive_one(ws)

            if kind == "text" and value == {"type": "reply_finished"}:
                break

        # Reconnected rather than leaving the caller unheard for the rest of
        # the call - the whole point. Polled because the reconnect is a
        # background task with a deliberate delay in front of it, not
        # something the reply itself waits on.
        deadline = time.monotonic() + 5

        while mock_stt.call_count < 2 and time.monotonic() < deadline:
            time.sleep(0.05)

    assert mock_stt.call_count > 1, (
        "a stream that closed after transcribing must still be reconnected"
    )


def test_media_session_reconnecting_stt_does_not_starve_the_new_stream_of_audio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The reconnect must hand the replacement stream the caller's audio, not
    lose it to the stream it replaced.

    Each reconnect builds a fresh iterator over the same audio queue. The
    previous one is left suspended inside queue.get(), and that pending
    waiter keeps taking frames the new stream then never sees - so every
    reconnect leaves another thief behind and each new stream is starved a
    little further. Measured in a real session: reconnect counts past
    fifteen, every stream ending with zero events, while the caller's audio
    was arriving at full volume the whole time. The caller went entirely
    unheard, so nothing could interrupt anything.

    Two silent closes force two reconnects; the third stream must still
    receive enough audio to transcribe.
    """

    mock_stt = MockSTT(
        script=[TranscriptEvent(text="What are your hours?", is_final=True)],
        chunks_before_event=[3],
        silent_closes=2,
    )
    mock_llm = MockLLM(response="We open at nine.")
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
    monkeypatch.setattr(config, "STT_RECONNECT_DELAY_SECONDS", 0.01)
    _patch_turn_detector_vad(
        monkeypatch,
        _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET, VADState.QUIET]),
    )

    assistant_id = "00000000-0000-0000-0000-00000000001c"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        # Sent steadily rather than all at once, so a thieving iterator left
        # over from a previous stream has the chance to take some.
        for _ in range(12):
            ws.send_bytes(chunk)
            time.sleep(0.02)

        messages = []

        while True:
            kind, value = _receive_one(ws)

            if kind == "text":
                messages.append(value)

                if value == {"type": "reply_finished"}:
                    break

    assert mock_stt.call_count == 3, "both silent closes should have been reconnected"
    assert {"type": "turn_ended", "text": "What are your hours?"} in messages


def test_media_session_pipeline_has_no_idle_timeout() -> None:
    """
    Pipecat's idle watchdog cancels the pipeline - ending the call - after
    five minutes without a BotSpeakingFrame or UserSpeakingFrame. This
    pipeline emits neither, driving speech through Norma's own processors
    instead, so the watchdog saw no activity however busy the call was and
    hung up mid-answer on a caller who was still talking to it. Reported as
    "test call automatically ended in between answering", twice.

    Asserted on the constructed worker rather than by waiting five minutes,
    which no test suite should do.
    """

    worker = build_voice_session_pipeline_worker(
        MagicMock(),
        MockSTT(),
        MockLLM(),
        MockTTS(),
        assistant_id=uuid.uuid4(),
        call_id=uuid.uuid4(),
        language="en",
        keywords=(),
        sensitivity=0.5,
        system_prompt="You are a test assistant.",
        creativity=0.3,
        voice_id="voice-1",
        speech_rate=1.0,
        vad_analyzer=_ScriptedVADAnalyzer([VADState.QUIET]),
    )

    assert worker._idle_timeout_secs is None


def test_mostly_already_said_distinguishes_echo_from_a_real_interruption() -> None:
    """
    The guard that decides whether a mid-reply transcript is the caller
    genuinely talking over the assistant, or just the assistant's own
    playback (or the caller's own already-answered turn) coming back.
    Getting this wrong in either direction is a real, reported failure:
    too strict and every reply cuts itself off on a speaker setup, too
    loose and interruptions keep being ignored.
    """

    spoken = "We open at nine. We close at five."

    # The assistant's own sentence returning through an open mic, as STT
    # renders it - different casing and punctuation, same words.
    assert _mostly_already_said("we open at nine", spoken)
    assert _mostly_already_said("We close at five!", spoken)

    # A caller actually interrupting, including one sharing an ordinary
    # word with what is being said.
    assert not _mostly_already_said("stop", spoken)
    assert not _mostly_already_said("what about weekends", spoken)
    assert not _mostly_already_said("okay got it, cancel that", spoken)

    # Nothing to act on either way.
    assert _mostly_already_said("   ", spoken)
    assert not _mostly_already_said("anything at all", "")


def test_media_session_interruption_transcript_stops_the_reply_without_a_vad_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The reported bug: the caller speaks over a reply and has to sit through
    the rest of it before being answered. Turn detection latches for the
    whole reply, and the only signal that breaks that latch early -
    caller_speech_started - is a VAD onset *edge* the assistant's own audio
    in an open mic can hold high straight through the caller starting to
    talk. This scripts exactly that: VAD never reports a fresh onset after
    the first turn, so no caller_speech_started can fire, and the second
    final transcript is the only evidence the caller said anything. The
    reply must still be cut off.
    """

    # The interrupting final is held back until a fourth chunk arrives, which
    # the body below only sends once the reply is audibly under way - an
    # interruption has to land *during* a reply to be one at all.
    mock_stt = MockSTT(
        script=[
            TranscriptEvent(text="What are your hours?", is_final=True),
            TranscriptEvent(text="Actually cancel that.", is_final=True),
        ],
        chunks_before_event=[1, 4],
    )
    mock_llm = MockLLM(response="We open at nine. We close at five. See you then.")
    # Slow enough that the reply is unambiguously still in flight when the
    # interrupting transcript lands.
    mock_tts = MockTTS(time_to_first_byte_seconds=0.4)

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "get_llm_provider", lambda: mock_llm)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity)
    _patch_session_setup(monkeypatch)
    monkeypatch.setattr(main_module, "get_tts_provider", lambda: mock_tts)
    monkeypatch.setattr(
        media_session_module, "fetch_retrieved_context", _fake_fetch_retrieved_context
    )
    # SPEAKING only for the very first chunk, then quiet forever: the first
    # turn gets its onset edge, the interruption never does.
    _patch_turn_detector_vad(
        monkeypatch,
        _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET, VADState.QUIET]),
    )

    assistant_id = "00000000-0000-0000-0000-000000000018"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        messages = []

        # Wait until the reply is actually being spoken before interrupting.
        while True:
            kind, value = _receive_one(ws)

            if kind == "text":
                messages.append(value)
            elif kind == "bytes":
                break

        # The fourth chunk releases the interrupting final transcript.
        ws.send_bytes(chunk)

        while True:
            kind, value = _receive_one(ws)

            if kind == "text":
                messages.append(value)

                # reply_finished here is the cancelled reply's own reset,
                # which is what proves it was cut off rather than played out.
                if value == {"type": "reply_finished"}:
                    break

    onsets = [message for message in messages if message == {"type": "caller_speech_started"}]

    assert len(onsets) == 1, (
        "only the caller's opening utterance should produce a VAD onset edge - "
        "a second one would mean this proves the old path, not the "
        "transcript-driven one"
    )
    assert mock_tts.cancelled, "the in-flight sentence should have been cancelled"
    # Cancelling server-side only stops *sending*. Audio already delivered is
    # scheduled for playback at the other end, so without this message the
    # caller keeps hearing the abandoned reply and the barge-in has no
    # audible effect whatsoever - the exact bug that made the first version
    # of this feature pass its tests while changing nothing on a real call.
    assert {"type": "playback_cancelled"} in messages


def test_media_session_does_not_treat_its_own_echo_as_an_interruption(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The other half, and the reason the guard exists at all: on the speaker
    setup this feature targets, the assistant's own playback is transcribed
    right back. Acting on that would cut every reply short mid-sentence -
    strictly worse than the late answer being fixed. Here the second final
    transcript is the assistant's own first sentence coming back, and the
    reply must play through both sentences untouched.
    """

    mock_stt = MockSTT(
        script=[
            TranscriptEvent(text="What are your hours?", is_final=True),
            TranscriptEvent(text="we open at nine", is_final=True),
        ],
        chunks_before_event=[1, 2],
    )
    mock_llm = MockLLM(response="We open at nine. We close at five.")
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

    assistant_id = "00000000-0000-0000-0000-000000000019"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        received = []
        expected_audio_length = (len("We open at nine.") + len("We close at five.")) * 320

        while (
            len(b"".join(value for kind, value in received if kind == "bytes"))
            < expected_audio_length
        ):
            received.append(_receive_one(ws))

    assert not mock_tts.cancelled
    assert mock_tts.call_count == 2


def test_media_session_carries_prosody_context_across_a_replys_sentences(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The bug fix behind "the assistant changes voice tone mid-call": a reply
    is synthesized one sentence at a time, and a provider handed each
    sentence in isolation restarts its prosody from scratch every time,
    audibly shifting tone, pitch and energy between sentences of what the
    caller hears as a single answer. Each sentence after the first must
    therefore carry the previous one as context. The first has nothing
    before it and correctly gets "".
    """

    final = TranscriptEvent(text="What are your hours?", is_final=True)
    mock_stt = MockSTT(script=[final], chunks_before_event=[1])
    mock_llm = MockLLM(response="We open at nine. We close at five.")
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

    assistant_id = "00000000-0000-0000-0000-000000000017"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        received = []

        while True:
            entry = _receive_one(ws)
            received.append(entry)

            if entry == ("text", {"type": "reply_finished"}):
                break

    assert mock_tts.call_count == 2
    assert mock_tts.received_previous_texts == ["", "We open at nine."]


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
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
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
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
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


def test_media_session_records_turn_metrics_for_a_normal_reply(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Item 20f's headline claim: a normal, uninterrupted turn posts exactly
    one TurnMetric record with every leg set, in the causal order the real
    pipeline actually produces them.
    """

    final = TranscriptEvent(text="What are your hours?", is_final=True)
    mock_stt = MockSTT(script=[final], chunks_before_event=[1])
    mock_llm = MockLLM(response="We open at nine.")
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
    calls = _capturing_record_turn_metric(monkeypatch)

    assistant_id = "00000000-0000-0000-0000-00000000000d"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        while True:
            entry = _receive_one(ws)

            if entry[0] == "text" and entry[1]["type"] == "reply_finished":
                break

    assert len(calls) == 1

    posted_assistant_id, record = calls[0]
    assert str(posted_assistant_id) == assistant_id
    assert record.stt_finalized_at is not None
    assert record.retrieval_done_at is not None
    assert record.llm_first_token_at is not None
    assert record.llm_complete_at is not None
    assert record.tts_first_byte_at is not None
    assert record.audio_out_at is not None

    assert record.stt_finalized_at <= record.retrieval_done_at
    assert record.retrieval_done_at <= record.llm_first_token_at
    assert record.llm_first_token_at <= record.tts_first_byte_at
    assert record.tts_first_byte_at <= record.audio_out_at


def test_media_session_records_a_null_audio_leg_on_tts_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A TTS-provider failure still writes a row - every LLM leg reached, but
    tts_first_byte_at/audio_out_at stay None since no audio was ever
    produced. "Every turn writes a row" tolerates a partial one.
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
    calls = _capturing_record_turn_metric(monkeypatch)

    assistant_id = "00000000-0000-0000-0000-00000000000b"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        while True:
            entry = _receive_one(ws)

            if entry[0] == "text" and entry[1]["type"] == "reply_finished":
                break

    assert len(calls) == 1

    _, record = calls[0]
    assert record.stt_finalized_at is not None
    assert record.retrieval_done_at is not None
    assert record.llm_first_token_at is not None
    assert record.llm_complete_at is not None
    assert record.tts_first_byte_at is None
    assert record.audio_out_at is None


def test_media_session_barge_in_posts_two_separate_turn_metric_records(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A barge-in must never merge or corrupt one turn's record with the
    next's - the interrupted first turn's own (partial: no audio_out_at)
    record and the interrupting second turn's own (complete) record are
    posted separately.
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
    calls = _capturing_record_turn_metric(monkeypatch)

    assistant_id = "00000000-0000-0000-0000-00000000000c"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        while True:
            entry = _receive_one(ws)

            if entry[0] == "text" and entry[1]["type"] == "llm_complete":
                break

        # Interrupt before any audio for turn 1 was ever sent.
        for _ in range(3):
            ws.send_bytes(chunk)

        # Two reply_finished messages arrive in total: turn 1's own (pushed
        # by the barge-in reset itself) arrives almost immediately, well
        # before turn 2 has produced any audio - wait for the *second* one,
        # which is turn 2's own normal completion.
        reply_finished_count = 0

        while reply_finished_count < 2:
            entry = _receive_one(ws)

            if entry[0] == "text" and entry[1]["type"] == "reply_finished":
                reply_finished_count += 1

    assert len(calls) == 2

    _, first_record = calls[0]
    _, second_record = calls[1]

    assert first_record.call_id == second_record.call_id

    # Turn 1 was interrupted before any audio ever played.
    assert first_record.stt_finalized_at is not None
    assert first_record.llm_complete_at is not None
    assert first_record.audio_out_at is None

    # Turn 2 completed normally, and is a genuinely distinct turn.
    assert second_record.stt_finalized_at is not None
    assert second_record.audio_out_at is not None
    assert second_record.stt_finalized_at != first_record.stt_finalized_at


def test_media_session_disconnecting_mid_reply_still_posts_a_partial_record(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A real gap found while red-teaming this feature's spec: _reset_turn()
    is the only normal flush point, but a caller hanging up mid-reply never
    reaches it. Closing the connection before TTS ever produces audio must
    still post whatever legs the turn reached, via the EndFrame/CancelFrame
    path, not silently lose the row.
    """

    final = TranscriptEvent(text="What are your hours?", is_final=True)
    mock_stt = MockSTT(script=[final], chunks_before_event=[1])
    mock_llm = MockLLM(response="We open at nine.")
    # Long enough that no audio can possibly arrive before the test closes
    # the connection below.
    mock_tts = MockTTS(time_to_first_byte_seconds=100.0)

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
    calls = _capturing_record_turn_metric(monkeypatch)

    assistant_id = "00000000-0000-0000-0000-00000000000e"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        # Read only through llm_complete - TTS is still asleep inside its
        # own very long time_to_first_byte_seconds. Exiting the `with`
        # block now closes the connection mid-reply, before reply_finished
        # (and therefore before _reset_turn()) ever runs.
        while True:
            entry = _receive_one(ws)

            if entry[0] == "text" and entry[1]["type"] == "llm_complete":
                break

    assert len(calls) == 1

    _, record = calls[0]
    assert record.stt_finalized_at is not None
    assert record.llm_complete_at is not None
    assert record.audio_out_at is None


def test_media_session_disconnecting_before_any_turn_starts_posts_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A session that never has a single turn (the caller connects and hangs
    up without ever speaking) has nothing worth recording - the
    EndFrame/CancelFrame flush must not post an entirely empty record.
    """

    mock_stt = MockSTT(script=[], chunks_before_event=[])
    mock_llm = MockLLM()
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
    _patch_turn_detector_vad(monkeypatch, _ScriptedVADAnalyzer([VADState.QUIET]))
    calls = _capturing_record_turn_metric(monkeypatch)

    assistant_id = "00000000-0000-0000-0000-00000000000f"

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        ws.send_bytes(bytes(range(256)) * 5)

    assert calls == []


def test_media_session_stt_crash_triggers_session_failover_after_retries_exhausted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Item 20g: an STT provider that crashes mid-call - the caller is still
    actively talking, no scripted event or fallback timeout has ended a
    turn - must not just go silently dead. fail_without_draining lets
    MockSTT raise without first waiting for the (never-arriving) end of a
    live connection's audio - see its own docstring. A persistent failure
    (every stream() call raises) still exhausts MAX_STT_STREAM_RETRIES and
    fails over exactly as before the retry was added - only a transient
    failure that later recovers (see the retry-recovers test below) is new
    behavior.
    """

    mock_stt = MockSTT(
        script=[],
        chunks_before_event=[],
        failure=SpeechProviderUnavailable("boom"),
        fail_without_draining=True,
    )

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity)
    _patch_session_setup(monkeypatch)
    _patch_turn_detector_vad(monkeypatch, _ScriptedVADAnalyzer([VADState.QUIET]))

    assistant_id = "00000000-0000-0000-0000-000000000010"

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        received = []

        while True:
            entry = _receive_one(ws)
            received.append(entry)

            if entry[0] == "text" and entry[1]["type"] == "session_failover":
                break

    assert all(kind == "text" for kind, _ in received)
    text_messages = [value for _, value in received]
    # playback_cancelled precedes it: failover drops whatever the abandoned
    # reply had already been delivered to the client, so the apology is not
    # heard queued behind it.
    assert text_messages == [
        {"type": "playback_cancelled"},
        {
            "type": "session_failover",
            "reason": "stt_unavailable",
            "message": (
                "I'm sorry, I'm having trouble with the call right now. "
                "Please try again in a few minutes."
            ),
        },
    ]
    # At least the original attempts; it keeps retrying afterwards rather
    # than abandoning the caller, so this is a floor, not an exact count.
    assert mock_stt.call_count >= config.MAX_STT_STREAM_RETRIES + 1


def test_media_session_stt_stream_retries_and_recovers_from_a_transient_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Item 20g (raised for the "test call automatically ended" bug): a single
    STT stream error that the next reconnect attempt survives must not end
    the call at all - the caller keeps talking and gets transcribed once
    the fresh stream is up, exactly as if nothing had happened. fail_times=1
    caps MockSTT's failure to only the first stream() call; the second call
    (the retry) succeeds and re-yields the same scripted transcript, proving
    a real reconnect - not just a swallowed exception.
    """

    transcript = TranscriptEvent(text="What are your hours?", is_final=True)
    mock_stt = MockSTT(
        script=[transcript],
        failure=SpeechProviderUnavailable("boom"),
        fail_without_draining=True,
        fail_times=1,
    )

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity)
    _patch_session_setup(monkeypatch)
    _patch_turn_detector_vad(monkeypatch, _ScriptedVADAnalyzer([VADState.QUIET]))

    assistant_id = "00000000-0000-0000-0000-000000000015"

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        received = []

        # The retry is silent (no message marks a successful reconnect), so
        # synchronize on the transcript itself: call 1 yields it then
        # raises, the retry's call 2 yields it again before returning
        # cleanly - receiving it twice proves the second stream() call ran.
        for _ in range(2):
            received.append(_receive_one(ws))

    assert all(kind == "text" for kind, _ in received)
    text_messages = [value for _, value in received]
    assert text_messages == [
        {"type": "transcript", "text": "What are your hours?", "is_final": True},
        {"type": "transcript", "text": "What are your hours?", "is_final": True},
    ]
    assert mock_stt.call_count == 2


def _patch_short_llm_timeout(monkeypatch: pytest.MonkeyPatch, *, seconds: float = 0.05) -> None:
    monkeypatch.setattr(config, "LLM_FIRST_TOKEN_TIMEOUT_SECONDS", seconds)


def test_media_session_llm_that_never_responds_is_retried_before_giving_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Item 20g: a provider that only ever times out (never raises) must
    still be retried the configured number of times, not just abandoned
    after the first attempt - proven via call_count, since MockLLM's
    chunk_delay_seconds makes every attempt equally slow.
    """

    final = TranscriptEvent(text="What are your hours?", is_final=True)
    mock_stt = MockSTT(script=[final], chunks_before_event=[1])
    mock_llm = MockLLM(response="We open at nine.", chunk_delay_seconds=0.2)

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
    _patch_short_llm_timeout(monkeypatch)

    assistant_id = "00000000-0000-0000-0000-000000000011"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        received = []

        while True:
            entry = _receive_one(ws)
            received.append(entry)

            if entry[0] == "text" and entry[1]["type"] == "llm_error":
                break

    assert mock_llm.call_count == config.MAX_PROVIDER_RETRIES + 1
    assert all(kind == "text" for kind, _ in received)


def test_media_session_llm_failover_only_after_consecutive_failure_threshold(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Item 20g: an isolated LLM blip still just gets the existing llm_error -
    session_failover only appears once MAX_CONSECUTIVE_LLM_FAILURES
    *separate* turns have all failed in a row.
    """

    monkeypatch.setattr(config, "MAX_CONSECUTIVE_LLM_FAILURES", 2)

    first_final = TranscriptEvent(text="First question.", is_final=True)
    second_final = TranscriptEvent(text="Second question.", is_final=True)
    mock_stt = MockSTT(script=[first_final, second_final], chunks_before_event=[1, 4])
    mock_llm = MockLLM(response="We open at nine.", chunk_delay_seconds=0.2)

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
    _patch_short_llm_timeout(monkeypatch)

    assistant_id = "00000000-0000-0000-0000-000000000012"
    chunk = bytes(range(256)) * 5
    first_turn_messages = []
    second_turn_messages = []

    try:
        with (
            TestClient(app) as client,
            client.websocket_connect(_media_session_url(assistant_id)) as ws,
        ):
            for _ in range(3):
                ws.send_bytes(chunk)

            while True:
                entry = _receive_one(ws)
                first_turn_messages.append(entry)

                if entry[0] == "text" and entry[1]["type"] == "llm_error":
                    break

            for _ in range(3):
                ws.send_bytes(chunk)

            while True:
                entry = _receive_one(ws)
                second_turn_messages.append(entry)

                if entry[0] == "text" and entry[1]["type"] == "session_failover":
                    break
    except FutureCancelledError:
        # session_failover leads to TTSProcessor speaking an apology and
        # ending the pipeline - the same server-initiated-close race
        # test_media_session_session_failover_speaks_apology_and_closes_the_connection
        # documents, reachable here too since this test stops reading as
        # soon as it sees session_failover, without draining the apology
        # and close that follow. Both message lists are already populated
        # by this point, so the assertions below are unaffected.
        pass

    first_turn_types = [value["type"] for kind, value in first_turn_messages if kind == "text"]
    assert "llm_error" in first_turn_types
    assert "session_failover" not in first_turn_types

    second_turn_types = [
        value["type"] for kind, value in second_turn_messages if kind == "text"
    ]
    assert "llm_error" in second_turn_types
    assert "session_failover" in second_turn_types


def test_media_session_tts_that_never_responds_is_retried_before_giving_up(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Item 20g's TTS-side counterpart: a provider that only ever times out
    (never raises) is still retried the configured number of times before
    tts_error is pushed for that sentence - proven via call_count, mirroring
    the LLM-side test above. _play_sentences's own per-sentence loop
    (proven independently by the pre-existing tts-failure test) is
    unchanged, so a later sentence continuing normally after this one
    fails needs no separate proof here.
    """

    final = TranscriptEvent(text="Can you help me?", is_final=True)
    mock_stt = MockSTT(script=[final], chunks_before_event=[1])
    mock_llm = MockLLM(response="Sure thing.")
    mock_tts = MockTTS(time_to_first_byte_seconds=0.2)

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
    monkeypatch.setattr(config, "TTS_FIRST_BYTE_TIMEOUT_SECONDS", 0.05)

    assistant_id = "00000000-0000-0000-0000-000000000013"
    chunk = bytes(range(256)) * 5

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        for _ in range(3):
            ws.send_bytes(chunk)

        received = []

        while True:
            entry = _receive_one(ws)
            received.append(entry)

            if entry[0] == "text" and entry[1]["type"] == "tts_error":
                break

    assert mock_tts.call_count == config.MAX_PROVIDER_RETRIES + 1
    assert all(kind == "text" for kind, _ in received)


def test_media_session_session_failover_speaks_apology_and_keeps_the_call_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The failover apology is spoken, and - the part this test now exists to
    hold - the connection stays open afterwards.

    It used to assert the opposite: an EndFrame was pushed and the socket
    closed server-side. That behavior was removed because it hung up on
    the caller mid-session, reported twice as "the test call ended
    automatically". A test call ends when the person on it disconnects.
    The apology tells them something went wrong while the STT stream keeps
    being retried underneath.
    """

    mock_stt = MockSTT(
        script=[],
        chunks_before_event=[],
        failure=SpeechProviderUnavailable("boom"),
        fail_without_draining=True,
    )
    mock_tts = MockTTS()

    monkeypatch.setattr(main_module, "get_stt_provider", lambda: mock_stt)
    monkeypatch.setattr(main_module, "fetch_glossary_terms", _fake_fetch_glossary_terms)
    monkeypatch.setattr(main_module, "fetch_turn_sensitivity", _fake_fetch_turn_sensitivity)
    _patch_session_setup(monkeypatch)
    monkeypatch.setattr(main_module, "get_tts_provider", lambda: mock_tts)
    _patch_turn_detector_vad(monkeypatch, _ScriptedVADAnalyzer([VADState.QUIET]))

    assistant_id = "00000000-0000-0000-0000-000000000014"
    received = []
    closed_by_server = False

    with (
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id)) as ws,
    ):
        # Read up to the spoken apology, then stop - there is deliberately
        # no close to wait for any more.
        while not any(
            kind == "text" and value.get("type") == "session_failover"
            for kind, value in received
        ) or not any(kind == "bytes" for kind, _ in received):
            message = ws.receive()

            if message["type"] == "websocket.close":
                closed_by_server = True
                break

            if "bytes" in message:
                received.append(("bytes", message["bytes"]))
            else:
                received.append(("text", json.loads(message["text"])))

    assert not closed_by_server, "the call must outlive a provider failure"

    # playback_cancelled first, so the apology below is not heard queued
    # behind whatever of the abandoned reply the client already holds.
    assert received[0] == ("text", {"type": "playback_cancelled"})
    assert received[1] == (
        "text",
        {
            "type": "session_failover",
            "reason": "stt_unavailable",
            "message": (
                "I'm sorry, I'm having trouble with the call right now. "
                "Please try again in a few minutes."
            ),
        },
    )
    assert any(kind == "bytes" for kind, _ in received)
    assert mock_tts.call_count == 1


def test_media_session_rejects_a_connection_with_no_ticket(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    ticket is a required query parameter - FastAPI itself refuses the
    handshake before app code (and therefore before .accept()) ever runs,
    which is an even stronger guarantee than main.py's own rejection path
    below for a present-but-invalid ticket.
    """

    _patch_session_setup(monkeypatch)

    with (
        pytest.raises(WebSocketDisconnect),
        TestClient(app) as client,
        client.websocket_connect("/media/session") as ws,
    ):
        ws.receive()


def test_media_session_rejects_an_expired_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session_setup(monkeypatch)

    assistant_id = "00000000-0000-0000-0000-000000000015"
    expired_ticket = _test_ticket(assistant_id, ttl_seconds=-1)

    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id, ticket=expired_ticket)) as ws,
    ):
        ws.receive()

    assert exc_info.value.code == 4401


def test_media_session_rejects_a_tampered_ticket(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_session_setup(monkeypatch)

    assistant_id = "00000000-0000-0000-0000-000000000016"
    ticket = _test_ticket(assistant_id)
    # Flip a character well inside the signature segment - see
    # test_voice_session_ticket.py's identical precedent for why not the
    # very last character.
    tampered = ticket[:-5] + ("A" if ticket[-5] != "A" else "B") + ticket[-4:]

    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id, ticket=tampered)) as ws,
    ):
        ws.receive()

    assert exc_info.value.code == 4401


def test_media_session_rejects_a_token_of_the_wrong_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    A well-formed, correctly-signed token that simply is not a voice-session
    ticket (an apps/api access token, say) must not be accepted either.
    """

    _patch_session_setup(monkeypatch)

    assistant_id = "00000000-0000-0000-0000-000000000017"
    other_token = jwt.encode(
        {"sub": assistant_id, "type": "access", "exp": 9999999999},
        _TEST_SECRET_KEY,
        algorithm=_TEST_JWT_ALGORITHM,
    )

    with (
        pytest.raises(WebSocketDisconnect) as exc_info,
        TestClient(app) as client,
        client.websocket_connect(_media_session_url(assistant_id, ticket=other_token)) as ws,
    ):
        ws.receive()

    assert exc_info.value.code == 4401
