"""
Conversation-level tests using the reusable harness (conversation_harness.py,
build-plan item 22). See that module's docstring for where the rest of the
pipeline's behavioral coverage already lives.
"""

from norma_shared.mock_speech import MockSTT
from norma_shared.speech import TranscriptEvent
from pipecat.audio.vad.vad_analyzer import VADState

from app.conversation import Message
from app.mock_llm import MockLLM
from app.turn_detection import FALLBACK_TIMEOUT_SECONDS
from tests.conversation_harness import (
    open_conversation_session,
    receive_until,
    send_audio_chunks,
)


def test_conversation_replay_answers_a_simple_question(monkeypatch) -> None:
    """
    A caller asks a question and gets a real answer - the same scenario
    test_media_session_streams_an_llm_reply_after_a_turn_ends already proves
    by hand, replayed here through the harness to demonstrate what it saves:
    a handful of lines instead of the ~15 that test hand-wires inline.
    """

    mock_stt = MockSTT(
        script=[TranscriptEvent(text="What are your hours?", is_final=True)],
        chunks_before_event=[1],
    )
    mock_llm = MockLLM(response="We are open nine to five.", chunk_words=3)

    with open_conversation_session(
        monkeypatch,
        mock_stt=mock_stt,
        mock_llm=mock_llm,
        vad_states=[VADState.SPEAKING, VADState.QUIET, VADState.QUIET],
    ) as ws:
        send_audio_chunks(ws, 3)
        trace = receive_until(ws, stop_types={"reply_finished"})

    llm_complete = next(
        payload
        for kind, payload in trace
        if kind == "text" and payload["type"] == "llm_complete"
    )

    assert llm_complete["text"] == "We are open nine to five."


def test_conversation_replay_answers_the_real_question_after_an_interruption(
    monkeypatch,
) -> None:
    """
    Genuinely new coverage (see this feature's spec): existing barge-in
    tests prove the internal mechanics - the interrupted first turn's LLM
    call is cancelled and a second turn is detected - but stop there. This
    proves the caller-visible outcome the mechanics exist for: after
    interrupting with a different question, the caller gets a real,
    complete answer to what they actually asked the second time, not to
    the question they were cut off asking.
    """

    first_final = TranscriptEvent(text="What are your hours?", is_final=True)
    second_final = TranscriptEvent(text="What is your address?", is_final=True)
    mock_stt = MockSTT(script=[first_final, second_final], chunks_before_event=[1, 5])
    mock_llm = MockLLM(response="123 Main Street.", chunk_delay_seconds=0.2)

    with open_conversation_session(
        monkeypatch,
        mock_stt=mock_stt,
        mock_llm=mock_llm,
        vad_states=[
            VADState.SPEAKING,
            VADState.QUIET,
            VADState.SPEAKING,
            VADState.SPEAKING,
            VADState.QUIET,
        ],
    ) as ws:
        # Turn 1 starts and its LLM call goes in flight (MockLLM is
        # sleeping chunk_delay_seconds before its first delta).
        send_audio_chunks(ws, 2)
        receive_until(ws, stop_types={"turn_ended"})

        # Interrupt with turn 2's audio before turn 1's reply ever starts -
        # cancels the in-flight call (mirrors
        # test_media_session_cancels_an_in_flight_llm_call_on_caller_speech_started's
        # exact scripting, extended past where that test stops).
        send_audio_chunks(ws, 1)
        receive_until(ws, stop_types={"reply_finished"})

        send_audio_chunks(ws, 2)
        receive_until(ws, stop_types={"turn_ended"})

        # Turn 2 is now a genuine, uninterrupted turn - it runs to a real
        # completed reply.
        trace = receive_until(ws, stop_types={"reply_finished"})

    turn_2_llm_complete = next(
        payload
        for kind, payload in trace
        if kind == "text" and payload["type"] == "llm_complete"
    )
    assert turn_2_llm_complete["text"] == "123 Main Street."

    # The proof that this is a real answer to the *second* question, not a
    # stale reuse of the first: MockLLM.stream() is called once per turn
    # (call_count == 2, including turn 1's cancelled call), and its most
    # recent invocation's own message - the one this reply was actually
    # generated from - is the caller's real second question. (The first,
    # interrupted question legitimately stays in conversation history
    # earlier in the list; barge-in cancels the reply, not the fact that
    # the caller said something.)
    assert mock_llm.call_count == 2
    assert mock_llm.received_messages[-1] == Message(role="user", content="What is your address?")


class _FakeClock:
    """Mirrors test_turn_detection.py's own _FakeClock - a mutable value, not real time."""

    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value


def test_conversation_replay_ends_the_turn_on_the_fallback_timeout(monkeypatch) -> None:
    """
    Genuinely new coverage (see this feature's spec): the fallback-timeout
    path (app/turn_detection.py's FALLBACK_TIMEOUT_SECONDS) is only
    unit-tested against a bare TurnDetector today
    (test_turn_detection.py::test_the_fallback_timeout_ends_the_turn_despite_an_incomplete_transcript).
    This proves the same behavior through the real WebSocket pipeline,
    using an injected fake clock so the test is fast and deterministic
    instead of a real 3+ second sleep.
    """

    incomplete = TranscriptEvent(text="I need an appointment and", is_final=True)
    mock_stt = MockSTT(script=[incomplete], chunks_before_event=[2])
    mock_llm = MockLLM(response="Go ahead.", chunk_words=2)
    clock = _FakeClock()

    with open_conversation_session(
        monkeypatch,
        mock_stt=mock_stt,
        mock_llm=mock_llm,
        vad_states=[VADState.SPEAKING, VADState.QUIET],
        clock=clock,
    ) as ws:
        # Speech, then silence with an incomplete-sounding transcript - the
        # turn must not end yet (mirrors the unit test's own first half).
        send_audio_chunks(ws, 2)
        before_timeout = receive_until(ws, stop_types={"transcript"})

        assert not any(
            kind == "text" and payload["type"] == "turn_ended"
            for kind, payload in before_timeout
        )

        # Cross FALLBACK_TIMEOUT_SECONDS without any new speech, then send
        # one more (still-silent) chunk - the pipeline only rechecks
        # turn_ended() when a new frame arrives, exactly like the bare
        # TurnDetector unit test's own second feed_audio() call.
        clock.value = FALLBACK_TIMEOUT_SECONDS + 0.1
        send_audio_chunks(ws, 1)
        after_timeout = receive_until(ws, stop_types={"turn_ended"})

    turn_ended = next(
        payload
        for kind, payload in after_timeout
        if kind == "text" and payload["type"] == "turn_ended"
    )
    assert turn_ended["text"] == "I need an appointment and"
