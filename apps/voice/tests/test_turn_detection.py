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


async def test_the_fallback_timeout_does_not_end_the_turn_on_an_empty_transcript() -> None:
    """
    Regression guard for a real reported bug: a VAD false trigger (background
    noise, a mic pop, a breath, the assistant's own TTS bleeding into the
    mic) sets ever_spoken without the caller ever saying anything STT could
    transcribe. The fallback timeout must not end a turn - and send an empty
    message to the LLM - over silence; it should only fire for a transcript
    that actually has content, however incomplete.
    """

    clock = _FakeClock()
    vad = _ScriptedVADAnalyzer(
        [VADState.SPEAKING, VADState.QUIET, VADState.QUIET, VADState.SPEAKING, VADState.QUIET]
    )
    detector = TurnDetector(sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad, clock=clock)

    await detector.feed_audio(b"noise")

    clock.value = 1.0
    await detector.feed_audio(b"silence")
    assert detector.turn_ended() is False

    clock.value = 1.0 + FALLBACK_TIMEOUT_SECONDS
    await detector.feed_audio(b"still silence")

    assert detector.turn_ended() is False

    # Detection cleanly rearms for the caller's real next turn rather than
    # getting stuck - a fresh speech/silence cycle still ends a turn once a
    # real transcript arrives.
    clock.value = 2.0 + FALLBACK_TIMEOUT_SECONDS
    await detector.feed_audio(b"real speech")
    clock.value = 2.5 + FALLBACK_TIMEOUT_SECONDS
    await detector.feed_audio(b"real silence")
    detector.feed_transcript("Hello there.", is_final=True)

    assert detector.turn_ended() is True
    assert detector.last_final_transcript == "Hello there."


async def test_an_empty_final_transcript_does_not_erase_what_the_caller_said() -> None:
    """
    Regression guard for a real reported bug: ElevenLabs' realtime STT emits
    committed_transcript events with empty text (verified directly against
    the live API), often trailing a genuine one. Letting an empty final
    overwrite the pending transcript erased the caller's actual words, and
    the turn could then never end - no reply, and a blank transcript.
    """

    clock = _FakeClock()
    vad = _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET])
    detector = TurnDetector(sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad, clock=clock)

    await detector.feed_audio(b"speech")
    detector.feed_transcript("Hello there.", is_final=True)
    detector.feed_transcript("", is_final=True)

    clock.value = 0.5
    await detector.feed_audio(b"silence")

    assert detector.turn_ended() is True
    assert detector.last_final_transcript == "Hello there."


async def test_an_empty_final_transcript_alone_still_never_ends_a_turn() -> None:
    """
    The empty-final guard must not resurrect the "replies to silence" bug:
    an empty final with nothing before it still leaves nothing to say.
    """

    clock = _FakeClock()
    vad = _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET, VADState.QUIET])
    detector = TurnDetector(sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad, clock=clock)

    await detector.feed_audio(b"noise")
    detector.feed_transcript("", is_final=True)

    clock.value = 1.0
    await detector.feed_audio(b"silence")

    clock.value = 1.0 + FALLBACK_TIMEOUT_SECONDS
    await detector.feed_audio(b"still silence")

    assert detector.turn_ended() is False


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


def _speech_shaped_pcm(peak: int, *, sample_rate: int = 16000) -> bytes:
    """
    One second of speech-shaped noise at a given int16 peak.

    Noise rather than a tone because the thing being measured is loudness,
    and BS.1770 weights the voice band - a 1kHz sine and a voice at the same
    peak do not measure the same. One second because calculate_audio_volume
    needs at least a 400ms gating block.
    """

    import numpy as np

    rng = np.random.default_rng(7)
    samples = sample_rate
    spectrum = np.fft.rfft(rng.normal(size=samples))
    frequencies = np.fft.rfftfreq(samples, 1 / sample_rate)
    voice_band = np.where(
        (frequencies > 80) & (frequencies < 4000),
        1.0 / np.sqrt(np.maximum(frequencies, 80)),
        0.0,
    )
    signal = np.fft.irfft(spectrum * voice_band, samples)

    return (signal / np.max(np.abs(signal)) * peak).astype(np.int16).tobytes()


def test_the_fixed_floor_still_separates_speech_from_the_room_when_adaptation_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    VAD_ADAPTIVE_FLOOR=false falls back to one absolute threshold, so that
    path keeps the safety net it had - pinned against levels measured from
    real calls rather than a general claim about microphones.

    The distribution here is bimodal: background around a median peak of 448
    (-37 dBFS) reaching 1501 (-27 dBFS) at its loudest, speech at p90 of
    11466 (-9 dBFS). The loudest background must be rejected and real speech
    admitted.

    The adaptive floor, which is what actually runs, is covered in
    tests/test_adaptive_vad.py against the same three levels.
    """

    import importlib

    monkeypatch.setenv("VAD_ADAPTIVE_FLOOR", "false")

    from pipecat.audio.utils import calculate_audio_volume

    from app import turn_detection

    reloaded = importlib.reload(turn_detection)

    try:
        analyzer = reloaded._build_default_vad_analyzer(sensitivity=0.5, sample_rate=16000)
        floor = analyzer.params.min_volume

        speech = calculate_audio_volume(_speech_shaped_pcm(11466), 16000)
        loudest_background = calculate_audio_volume(_speech_shaped_pcm(1501), 16000)

        assert speech >= floor, (
            f"measured speech at -9 dBFS gives {speech:.3f}, below the {floor} "
            "floor - the caller would be transcribed and then ignored by turn "
            "detection, which they experience as silence"
        )
        assert loudest_background < floor, (
            f"measured background at -27 dBFS gives {loudest_background:.3f}, "
            f"at or above the {floor} floor - the room would be answered as "
            "if it were the caller"
        )
        assert analyzer.params.confidence > 0.7
    finally:
        monkeypatch.undo()
        importlib.reload(turn_detection)


def test_the_vad_thresholds_can_be_tuned_without_a_code_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    The right threshold depends on the room and the handset, so it has to be
    reachable without a redeploy.
    """

    import importlib

    monkeypatch.setenv("VAD_CONFIDENCE", "0.95")
    monkeypatch.setenv("VAD_MIN_VOLUME", "0.85")
    # The absolute floor is only the decision-maker with adaptation off; on,
    # it is deliberately slackened so this call's own background decides
    # (see app/adaptive_vad.py).
    monkeypatch.setenv("VAD_ADAPTIVE_FLOOR", "false")

    from app import turn_detection

    reloaded = importlib.reload(turn_detection)
    try:
        analyzer = reloaded._build_default_vad_analyzer(sensitivity=0.5, sample_rate=16000)
        assert analyzer.params.confidence == 0.95
        assert analyzer.params.min_volume == 0.85
    finally:
        monkeypatch.undo()
        importlib.reload(turn_detection)


async def test_heard_speech_reports_whether_vad_has_heard_the_caller() -> None:
    """
    Half the answer to "why did this transcript not end the turn?", which
    was invisible and got the failure misdiagnosed twice: a stream could be
    delivering committed transcripts with no LLM call behind them and
    nothing said which condition was unmet.
    """

    vad = _ScriptedVADAnalyzer([VADState.QUIET, VADState.SPEAKING])
    detector = TurnDetector(sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad)

    assert detector.heard_speech is False

    await detector.feed_audio(b"silence")
    assert detector.heard_speech is False

    await detector.feed_audio(b"speech")
    assert detector.heard_speech is True


async def test_heard_speech_clears_once_a_turn_has_ended() -> None:
    """
    It answers "since the last turn", not "ever" - otherwise it would read
    True for the whole call and say nothing about the turn being diagnosed.
    """

    clock = _FakeClock()
    vad = _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET])
    detector = TurnDetector(
        sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad, clock=clock
    )

    await detector.feed_audio(b"speech")
    await detector.feed_audio(b"silence")
    detector.feed_transcript("What are your hours?", is_final=True)

    assert detector.turn_ended() is True
    assert detector.heard_speech is False


async def test_a_turn_never_ends_on_words_the_vad_never_heard() -> None:
    """
    Regression for "it randomly takes any voice and generates any question".

    The transcriber hears the whole call and commits anything it can make
    words out of - a television, a voice across the room - and it is far
    more willing to do that than the VAD is to call something speech. The
    damage is not that the text is stored, it is that a turn can then *end*
    on it: the caller makes some sound the VAD accepts, silence follows, and
    the pending transcript that gets sent to the model is a sentence from
    the room. The assistant answers a question nobody asked.

    Here the caller's own sound produces no transcript of its own - the
    realistic case, since the VAD accepts a cough or a chair before the
    transcriber has anything to commit.
    """

    clock = _FakeClock()
    vad = _ScriptedVADAnalyzer([VADState.QUIET, VADState.SPEAKING, VADState.QUIET])
    detector = TurnDetector(sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad, clock=clock)

    # The room, which the VAD does not accept as the caller.
    await detector.feed_audio(b"a television in the next room")
    detector.feed_transcript("Book me a table for four tonight.", is_final=True)

    # The caller makes a noise the VAD does accept, but says nothing the
    # transcriber commits.
    clock.value = 1.0
    await detector.feed_audio(b"a cough")

    # Then silence, long enough to end a turn.
    clock.value = 2.0
    await detector.feed_audio(b"silence")

    assert detector.turn_ended() is False, (
        "a turn ended carrying a sentence the VAD never attributed to the "
        "caller - the assistant would answer a question from the room"
    )
    assert detector.last_final_transcript == ""


async def test_a_transcript_arriving_just_after_the_caller_stops_is_still_theirs() -> None:
    """
    The guard must not eat genuine speech. A transcriber commits *after* the
    speech ends - it needs the silence to know the utterance is over - so
    the transcript for a real turn routinely arrives when the VAD has
    already gone quiet. What matters is that the VAD heard speech at some
    point in this turn, not that it is hearing it at the instant the words
    land.
    """

    clock = _FakeClock()
    vad = _ScriptedVADAnalyzer([VADState.SPEAKING, VADState.QUIET, VADState.QUIET])
    detector = TurnDetector(sensitivity=0.5, sample_rate=16_000, vad_analyzer=vad, clock=clock)

    await detector.feed_audio(b"the caller speaking")

    clock.value = 0.4
    await detector.feed_audio(b"they have stopped")

    # Only now does the transcriber commit.
    detector.feed_transcript("Can you book me in for Tuesday?", is_final=True)

    clock.value = 0.9
    await detector.feed_audio(b"still quiet")

    assert detector.turn_ended() is True
    assert detector.last_final_transcript == "Can you book me in for Tuesday?"
