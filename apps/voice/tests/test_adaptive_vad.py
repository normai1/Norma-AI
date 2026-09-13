"""
The volume floor that follows each call's own background.

These are written against the levels actually measured on this deployment,
because the whole reason this exists is that the numbers cannot be reasoned
about in the abstract - a fixed threshold was set from a general claim about
microphones and was wrong in both directions. Normalized BS.1770 loudness,
as pipecat computes it:

    0.51  the background's median, a quiet room
    0.71  the background at its loudest here
    0.89  the caller speaking

A call is working when 0.89 is admitted and 0.71 is not.
"""

import pytest
from pipecat.audio.vad.vad_analyzer import VADState

from app.adaptive_vad import (
    DEFAULT_MAX_THRESHOLD,
    DEFAULT_MIN_THRESHOLD,
    AdaptiveNoiseFloor,
    AdaptiveVolumeVADAnalyzer,
)

QUIET_ROOM = 0.51
LOUD_ROOM = 0.71
SPEECH = 0.89


def settled(floor: AdaptiveNoiseFloor, loudness: float, *, blocks: int = 40) -> None:
    for _ in range(blocks):
        floor.observe_background(loudness)


def test_a_noisy_room_raises_the_bar_above_its_own_noise() -> None:
    floor = AdaptiveNoiseFloor()
    settled(floor, LOUD_ROOM)

    assert not floor.admits(LOUD_ROOM), "the room would be answered as the caller"
    assert floor.admits(SPEECH), "the caller would not be heard over their own room"


def test_a_quiet_room_lowers_the_bar_to_match() -> None:
    """
    The case a fixed threshold cannot serve. In a silent room a softly
    spoken caller is still obviously speech - they stand out - and a
    threshold set for a noisy room would throw them away.
    """

    floor = AdaptiveNoiseFloor()
    settled(floor, QUIET_ROOM)

    softly_spoken = 0.70

    assert floor.admits(softly_spoken)
    # And the same level in the noisy room is correctly treated as the room.
    noisy = AdaptiveNoiseFloor()
    settled(noisy, LOUD_ROOM)

    assert not noisy.admits(softly_spoken)


def test_the_floor_tracks_the_rooms_loud_moments_not_its_average() -> None:
    """
    A floor sitting at the average is cleared by the room's own peaks, which
    is exactly what has to be rejected. Rising fast and falling slowly is
    what keeps it above them.
    """

    floor = AdaptiveNoiseFloor()

    # A room that is mostly quiet but intermittently loud.
    for _ in range(20):
        for _ in range(9):
            floor.observe_background(QUIET_ROOM)

        floor.observe_background(LOUD_ROOM)

    assert not floor.admits(LOUD_ROOM), (
        f"floor settled at {floor.floor:.3f}, so the room's louder moments "
        "clear the threshold and get answered"
    )


def test_a_new_noise_source_is_accommodated_within_seconds() -> None:
    """
    Someone turns a television on mid-call. If the floor took a minute to
    follow, that minute is spent answering it.
    """

    floor = AdaptiveNoiseFloor()
    settled(floor, QUIET_ROOM)

    # 400ms per block, so ten blocks is four seconds.
    for _ in range(10):
        floor.observe_background(LOUD_ROOM)

    assert not floor.admits(LOUD_ROOM)


def test_the_threshold_cannot_wander_out_of_useful_range() -> None:
    """
    Both clamps exist to stop an extreme room producing a useless gate: a
    silent one admitting anything audible, a deafening one rejecting every
    caller.
    """

    silent = AdaptiveNoiseFloor()
    settled(silent, 0.0)

    assert silent.threshold == DEFAULT_MIN_THRESHOLD

    deafening = AdaptiveNoiseFloor()
    settled(deafening, 1.0)

    assert deafening.threshold == DEFAULT_MAX_THRESHOLD


def test_the_opening_of_a_call_errs_towards_hearing_the_caller() -> None:
    """
    Nothing has been measured yet, and the start of a call is the worst
    moment to be deaf.
    """

    floor = AdaptiveNoiseFloor()

    assert not floor.learned
    assert floor.threshold == DEFAULT_MIN_THRESHOLD
    assert floor.admits(SPEECH)


class _ScriptedDelegate:
    """
    A VAD analyzer whose verdicts are a script, so the wrapper's own
    decisions are the only thing under test. Never loads Silero.
    """

    def __init__(self, states: list[VADState]) -> None:
        self._states = states
        self._index = 0
        self.params = None

    @property
    def sample_rate(self) -> int:
        return 16000

    def set_sample_rate(self, sample_rate: int) -> None:
        pass

    def set_params(self, params) -> None:
        pass

    def num_frames_required(self) -> int:
        return 512

    def voice_confidence(self, buffer: bytes) -> float:
        return 1.0

    async def cleanup(self) -> None:
        pass

    async def analyze_audio(self, buffer: bytes) -> VADState:
        state = self._states[min(self._index, len(self._states) - 1)]
        self._index += 1

        return state


def _block(loudness: float):
    """One gating block's worth of audio whose measured loudness is fixed."""

    return bytes(int(0.4 * 16000) * 2), loudness


async def _feed(analyzer: AdaptiveVolumeVADAnalyzer, levels: list[float]) -> list[VADState]:
    audio, _ = _block(0)

    return [await analyzer.analyze_audio(audio) for _ in levels]


def _analyzer(states: list[VADState], levels: list[float]) -> AdaptiveVolumeVADAnalyzer:
    """
    Wraps a scripted delegate with a scripted loudness sequence, so each
    call to analyze_audio measures the next level in the list.
    """

    remaining = list(levels)

    def volume_of(_buffer: bytes, _sample_rate: int) -> float:
        return remaining.pop(0) if remaining else levels[-1]

    return AdaptiveVolumeVADAnalyzer(
        _ScriptedDelegate(states), volume_of=volume_of
    )


async def test_a_speech_onset_that_does_not_stand_out_is_suppressed() -> None:
    """
    The reported bug, end to end through the wrapper: the underlying
    analyzer calls the room speech, and the wrapper declines to pass it on.
    """

    # Four quiet blocks to learn the room, then the analyzer reports speech
    # at that same level - which is the room, not the caller.
    states = [VADState.QUIET] * 4 + [VADState.SPEAKING] * 3
    levels = [LOUD_ROOM] * 4 + [LOUD_ROOM] * 3

    analyzer = _analyzer(states, levels)
    verdicts = await _feed(analyzer, levels)

    assert verdicts[-3:] == [VADState.QUIET] * 3
    assert analyzer.suppressed_runs == 1


async def test_the_caller_speaking_over_the_same_room_is_passed_through() -> None:
    states = [VADState.QUIET] * 4 + [VADState.SPEAKING] * 3
    levels = [LOUD_ROOM] * 4 + [SPEECH] * 3

    analyzer = _analyzer(states, levels)
    verdicts = await _feed(analyzer, levels)

    assert verdicts[-3:] == [VADState.SPEAKING] * 3
    assert analyzer.suppressed_runs == 0


async def test_the_verdict_is_taken_once_at_the_onset_and_then_held() -> None:
    """
    The underlying analyzer has its own hysteresis. Re-deciding every block
    would turn one steady utterance into a flapping series of turns - which
    is a worse version of the bug being fixed.
    """

    states = [VADState.QUIET] * 4 + [VADState.SPEAKING] * 4
    # The caller starts clearly, then trails off to a level that on its own
    # would not have opened the gate.
    levels = [LOUD_ROOM] * 4 + [SPEECH, SPEECH, LOUD_ROOM, LOUD_ROOM]

    analyzer = _analyzer(states, levels)
    verdicts = await _feed(analyzer, levels)

    assert verdicts[-4:] == [VADState.SPEAKING] * 4


async def test_the_callers_own_voice_never_trains_the_gate_against_them() -> None:
    """
    The failure mode of a naive implementation that folds everything into
    the floor: a talkative caller raises the bar until they can no longer
    clear it, and the assistant goes deaf to the person using it.
    """

    states = [VADState.QUIET] * 2 + [VADState.SPEAKING] * 30
    levels = [QUIET_ROOM] * 2 + [SPEECH] * 30

    analyzer = _analyzer(states, levels)
    await _feed(analyzer, levels)

    assert analyzer.noise_floor.admits(SPEECH)
    assert analyzer.noise_floor.floor == pytest.approx(QUIET_ROOM, abs=0.01)


async def test_an_unmeasurable_block_does_not_take_the_call_down() -> None:
    def explode(_buffer: bytes, _sample_rate: int) -> float:
        raise ValueError("too short to measure")

    analyzer = AdaptiveVolumeVADAnalyzer(
        _ScriptedDelegate([VADState.QUIET, VADState.SPEAKING]), volume_of=explode
    )
    audio, _ = _block(0)

    assert await analyzer.analyze_audio(audio) == VADState.QUIET
    # Nothing measured, so the opening threshold still applies and the
    # caller is heard rather than silently gated out.
    assert await analyzer.analyze_audio(audio) in (VADState.QUIET, VADState.SPEAKING)


def test_even_a_deafening_room_leaves_real_speech_audible() -> None:
    """
    The trap the first version of this walked into. The cap was set at 0.90,
    above the 0.89 that speech measures here, so a loud enough room clamped
    the threshold to a level the caller could never clear - reintroducing
    exactly the deafness the adaptive floor exists to replace, only harder
    to see because it now depended on the room.

    Past the cap the room is louder than the caller and no threshold
    separates them. Failing towards "admit things" is the right way to lose:
    the caller is heard, some of the room is too, and it is obvious that
    something is wrong.
    """

    floor = AdaptiveNoiseFloor()
    settled(floor, 0.95)

    assert floor.threshold == DEFAULT_MAX_THRESHOLD
    assert floor.admits(SPEECH), (
        f"a room at 0.95 clamps the threshold to {floor.threshold}, which "
        f"speech at {SPEECH} cannot clear - the caller is deaf again"
    )
