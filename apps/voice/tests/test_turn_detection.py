from collections.abc import Sequence

import pytest
from pipecat.audio.vad.vad_analyzer import VADState

from app.turn_detection import (
    FALLBACK_TIMEOUT_SECONDS,
    TurnDetector,
    is_semantically_complete,
    sensitivity_to_stop_secs,
)


class _ScriptedVADAnalyzer:
    """
    Returns one VADState per analyze_audio() call, in order (the last entry
    repeats once exhausted) - never loads the real Silero model.
    """

    def __init__(self, states: Sequence[VADState]) -> None:
        self._states = list(states)
        self._index = 0

    def set_sample_rate(self, sample_rate: int) -> None:
        pass

    async def analyze_audio(self, buffer: bytes) -> VADState:
        state = self._states[min(self._index, len(self._states) - 1)]
        self._index += 1

        return state


class _FakeClock:
    def __init__(self, start: float = 0.0) -> None:
        self.value = start

    def __call__(self) -> float:
        return self.value


def test_sensitivity_to_stop_secs_is_bounded_and_monotonically_decreasing() -> None:
    most_patient = sensitivity_to_stop_secs(0.0)
    middle = sensitivity_to_stop_secs(0.5)
    most_eager = sensitivity_to_stop_secs(1.0)

    assert most_patient == pytest.approx(1.5)
    assert most_eager == pytest.approx(0.3)
    assert most_patient > middle > most_eager


def test_sensitivity_to_stop_secs_clamps_out_of_range_input() -> None:
    assert sensitivity_to_stop_secs(-1.0) == sensitivity_to_stop_secs(0.0)
    assert sensitivity_to_stop_secs(2.0) == sensitivity_to_stop_secs(1.0)


def test_is_semantically_complete_accepts_terminal_punctuation() -> None:
    assert is_semantically_complete("What time do you close?") is True
    assert is_semantically_complete("Book me in for Tuesday.") is True


def test_is_semantically_complete_rejects_a_trailing_continuation_word() -> None:
    assert is_semantically_complete("I need an appointment and") is False
    assert is_semantically_complete("so") is False


def test_is_semantically_complete_rejects_missing_punctuation() -> None:
    assert is_semantically_complete("I need an appointment") is False


def test_is_semantically_complete_rejects_empty_text() -> None:
    assert is_semantically_complete("") is False
    assert is_semantically_complete("   ") is False


async def test_turn_ends_once_silence_follows_a_semantically_complete_transcript() -> None:
    clock = _FakeClock()
    vad = _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET])
    detector = TurnDetector(sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad, clock=clock)

    await detector.feed_audio(b"speech")
    assert detector.turn_ended() is False

    clock.value = 0.5
    await detector.feed_audio(b"silence")
    assert detector.turn_ended() is False

    clock.value = 0.6
    detector.feed_transcript("Hello there.", is_final=True)

    assert detector.turn_ended() is True


async def test_an_incomplete_transcript_does_not_end_the_turn_immediately() -> None:
    clock = _FakeClock()
    vad = _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET])
    detector = TurnDetector(sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad, clock=clock)

    await detector.feed_audio(b"speech")

    clock.value = 1.0
    await detector.feed_audio(b"silence")
    detector.feed_transcript("and", is_final=True)

    assert detector.turn_ended() is False


async def test_the_fallback_timeout_ends_the_turn_despite_an_incomplete_transcript() -> None:
    clock = _FakeClock()
    vad = _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET, VADState.QUIET])
    detector = TurnDetector(sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad, clock=clock)

    await detector.feed_audio(b"speech")

    clock.value = 1.0
    await detector.feed_audio(b"silence")
    detector.feed_transcript("and", is_final=True)
    assert detector.turn_ended() is False

    clock.value = 1.0 + FALLBACK_TIMEOUT_SECONDS
    await detector.feed_audio(b"still silence")

    assert detector.turn_ended() is True


async def test_a_partial_transcript_never_ends_the_turn_on_its_own() -> None:
    clock = _FakeClock()
    vad = _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET])
    detector = TurnDetector(sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad, clock=clock)

    await detector.feed_audio(b"speech")

    clock.value = 0.5
    await detector.feed_audio(b"silence")
    detector.feed_transcript("Hello there.", is_final=False)

    assert detector.turn_ended() is False


async def test_silence_before_any_speech_never_ends_the_turn() -> None:
    clock = _FakeClock()
    vad = _ScriptedVADAnalyzer([VADState.QUIET])
    detector = TurnDetector(sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad, clock=clock)

    await detector.feed_audio(b"silence")
    detector.feed_transcript("Hello there.", is_final=True)

    clock.value = FALLBACK_TIMEOUT_SECONDS + 1.0
    await detector.feed_audio(b"still silence")

    assert detector.turn_ended() is False


async def test_reset_for_next_turn_detects_a_second_independent_turn() -> None:
    clock = _FakeClock()
    vad = _ScriptedVADAnalyzer(
        [VADState.SPEAKING, VADState.QUIET, VADState.SPEAKING, VADState.QUIET]
    )
    detector = TurnDetector(sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad, clock=clock)

    await detector.feed_audio(b"speech")
    clock.value = 0.5
    await detector.feed_audio(b"silence")
    detector.feed_transcript("Hello there.", is_final=True)
    assert detector.turn_ended() is True

    detector.reset_for_next_turn()
    assert detector.turn_ended() is False
    # last_final_transcript is a stable snapshot of the turn that just
    # ended, not cleared by reset - it still reads "Hello there." here,
    # and only changes once a *second* turn completes below.
    assert detector.last_final_transcript == "Hello there."

    clock.value = 1.0
    await detector.feed_audio(b"speech again")
    clock.value = 1.5
    await detector.feed_audio(b"silence again")
    detector.feed_transcript("Book me in for Tuesday.", is_final=True)

    assert detector.turn_ended() is True
    assert detector.last_final_transcript == "Book me in for Tuesday."


async def test_reset_for_next_turn_prevents_stale_text_from_ending_the_next_turn_early() -> None:
    """
    Regression guard for a real bug found while designing this reset: if
    last_final_transcript were not cleared, silence at the very start of
    the next turn - before any new final transcript arrives - would reuse
    the previous turn's already-complete sentence and end the new turn
    instantly.
    """

    clock = _FakeClock()
    vad = _ScriptedVADAnalyzer(
        [VADState.SPEAKING, VADState.QUIET, VADState.SPEAKING, VADState.QUIET]
    )
    detector = TurnDetector(sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad, clock=clock)

    await detector.feed_audio(b"speech")
    clock.value = 0.5
    await detector.feed_audio(b"silence")
    detector.feed_transcript("Hello there.", is_final=True)
    assert detector.turn_ended() is True

    detector.reset_for_next_turn()

    clock.value = 1.0
    await detector.feed_audio(b"speech again")
    clock.value = 1.5
    await detector.feed_audio(b"silence again")

    assert detector.turn_ended() is False


async def test_is_speaking_reflects_the_latest_vad_state() -> None:
    vad = _ScriptedVADAnalyzer([VADState.QUIET, VADState.SPEAKING, VADState.QUIET])
    detector = TurnDetector(sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad)

    assert detector.is_speaking is False

    await detector.feed_audio(b"silence")
    assert detector.is_speaking is False

    await detector.feed_audio(b"speech")
    assert detector.is_speaking is True

    await detector.feed_audio(b"silence again")
    assert detector.is_speaking is False


async def test_is_speaking_keeps_updating_while_turn_ended_is_latched() -> None:
    """
    Regression guard for item 20e's barge-in: feed_audio used to skip VAD
    analysis entirely once turn_ended() latched True, which would have
    made it impossible to ever detect the caller speaking during an
    in-flight reply.
    """

    clock = _FakeClock()
    vad = _ScriptedVADAnalyzer(
        [VADState.SPEAKING, VADState.QUIET, VADState.QUIET, VADState.SPEAKING]
    )
    detector = TurnDetector(sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad, clock=clock)

    await detector.feed_audio(b"speech")
    clock.value = 0.5
    await detector.feed_audio(b"silence")
    detector.feed_transcript("Hello there.", is_final=True)
    assert detector.turn_ended() is True

    await detector.feed_audio(b"still latched")
    assert detector.is_speaking is False

    await detector.feed_audio(b"caller interrupts")
    assert detector.is_speaking is True
    assert detector.turn_ended() is True


async def test_reset_for_next_turn_still_detects_a_short_interruption() -> None:
    """
    Regression guard for a real bug found via a hanging end-to-end barge-in
    test: the interrupting frame's own SPEAKING state was fed to feed_audio
    while turn_ended() was still True, so the early return meant it never
    set ever_spoken - and reset_for_next_turn() used to unconditionally
    clear ever_spoken back to False, discarding that fact entirely. If the
    caller's interruption is short (one SPEAKING frame, then straight to
    quiet - a plausible real interjection), turn_ended() could then never
    fire again, since silence_since could never be set. reset_for_next_turn
    must seed ever_spoken from is_speaking, not assume False.
    """

    clock = _FakeClock()
    vad = _ScriptedVADAnalyzer(
        [
            VADState.SPEAKING,
            VADState.QUIET,
            VADState.SPEAKING,  # the interruption - one frame, then quiet
            VADState.QUIET,
            VADState.QUIET,
        ]
    )
    detector = TurnDetector(sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad, clock=clock)

    await detector.feed_audio(b"speech")
    clock.value = 0.5
    await detector.feed_audio(b"silence")
    detector.feed_transcript("First question.", is_final=True)
    assert detector.turn_ended() is True

    # The interrupting frame is fed while still latched (turn_ended() is
    # still True here, exactly like the real pipeline) - only afterward
    # does the caller (TTSProcessor, in the real pipeline) call reset.
    await detector.feed_audio(b"short interruption")
    detector.reset_for_next_turn()

    clock.value = 1.0
    await detector.feed_audio(b"quiet")
    clock.value = 1.5
    await detector.feed_audio(b"still quiet")
    detector.feed_transcript("Second question.", is_final=True)

    assert detector.turn_ended() is True
    assert detector.last_final_transcript == "Second question."
