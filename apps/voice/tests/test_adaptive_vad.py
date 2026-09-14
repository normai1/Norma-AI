"""
The volume floor that follows each call's own background.

These are written against levels measured on this deployment, because the
whole reason this exists is that the numbers cannot be reasoned about in the
abstract. Normalized BS.1770 loudness, as pipecat computes it:

    0.51  a quiet room
    0.71  the background at its loudest here
    0.81  the caller, as their onsets actually measured on a live call

That last figure is a correction worth keeping. An earlier version of this
file used 0.89 for speech, taken from synthesising speech-shaped noise at the
p90 *peak* of the caller's audio. Real onsets on a real call measured 0.78 to
0.845 - a peak and a gating block's integrated loudness are not the same
quantity, and the difference is the entire margin between working and deaf.

Which is why the margin is not calibrated here and the gate ships off: with
the room at 0.71 and speech at 0.81 the usable margin is under 0.10, and
guessing it is what broke a live call. VAD_ADAPTIVE_FLOOR_SHADOW collects the
real distribution without acting on it.
"""

import math

import pytest
from pipecat.audio.vad.vad_analyzer import VADState

from app.adaptive_vad import (
    DEFAULT_MAX_THRESHOLD,
    DEFAULT_MIN_THRESHOLD,
    MAX_SUPPRESSED_RUN_SECONDS,
    ONSET_WINDOW_SECONDS,
    WINDOW_BLOCKS,
    AdaptiveNoiseFloor,
    AdaptiveVolumeVADAnalyzer,
)

QUIET_ROOM = 0.51
LOUD_ROOM = 0.71
SPEECH = 0.81  # measured on a live call, not synthesised


# How many blocks the underlying analyzer reports QUIET for after speech has
# actually started. Silero's start_secs is 0.2s and a block is 0.4s, so it is
# at least one - and one was enough to deafen a live call.
ONSET_LAG_BLOCKS = 1


def settled(
    floor: AdaptiveNoiseFloor, loudness: float, *, blocks: int = WINDOW_BLOCKS
) -> None:
    """Feed one level until it fills the window, so the floor is that level."""

    for _ in range(blocks):
        floor.observe(loudness)


def test_a_noisy_room_raises_the_bar_above_its_own_noise() -> None:
    floor = AdaptiveNoiseFloor()
    settled(floor, LOUD_ROOM)

    assert not floor.admits(LOUD_ROOM), "the room would be answered as the caller"
    assert floor.admits(SPEECH), "the caller would not be heard over their own room"


def test_the_same_voice_is_heard_in_a_quiet_room_and_ignored_in_a_loud_one() -> None:
    """
    The whole point, and the thing one absolute threshold cannot express.

    Deliberately not asserting that some particular level is "speech": the
    margin is not yet calibrated against real numbers, so a test naming a
    level would be pinning a guess. What must hold at any margin is that the
    verdict depends on the room, not on the level alone.
    """

    quiet = AdaptiveNoiseFloor()
    settled(quiet, QUIET_ROOM)

    loud = AdaptiveNoiseFloor()
    settled(loud, LOUD_ROOM)

    assert quiet.threshold < loud.threshold

    # A voice just loud enough to stand out in the quiet room.
    stands_out_when_quiet = quiet.threshold + 0.01

    assert quiet.admits(stands_out_when_quiet)
    assert not loud.admits(stands_out_when_quiet)


def test_the_callers_voice_cannot_raise_the_bar_against_them() -> None:
    """
    The regression that matters, and the one the first implementation failed
    in production.

    That version asked the analyzer which blocks were speech and folded the
    rest into the floor. The analyzer reports QUIET for the first fifth of a
    second of every utterance, so the caller's opening syllables - at full
    volume - were folded in as background. On a live call the floor climbed
    from 0.63 to 0.81 against a room measuring 0.71, hit its cap, and
    suppressed the caller for eight minutes.

    A minimum over a window cannot fail that way: however much the caller
    talks, they are never the quietest block in twelve seconds.
    """

    floor = AdaptiveNoiseFloor()

    # A caller who talks most of the time, over a steady room.
    for cycle in range(40):
        floor.observe(QUIET_ROOM if cycle % 4 == 0 else SPEECH)

    assert floor.floor == pytest.approx(QUIET_ROOM, abs=0.001), (
        f"the floor drifted to {floor.floor:.3f} - the caller is training "
        "the gate to ignore themselves"
    )
    assert floor.admits(SPEECH)


def test_a_room_that_genuinely_gets_louder_raises_the_floor() -> None:
    """
    The other half: the floor has to follow a real change, or a television
    switched on mid-call is answered for the rest of it.
    """

    floor = AdaptiveNoiseFloor()
    settled(floor, QUIET_ROOM)

    # The room's new level, for one window's worth of blocks.
    settled(floor, LOUD_ROOM)

    assert floor.floor == pytest.approx(LOUD_ROOM, abs=0.001)
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


def _scripted(states: list[VADState], levels: list[float], **kwargs):
    """
    A delegate and a loudness sequence, indexed by step rather than popped.

    Each analyze_audio call here carries exactly one gating block, and the
    wrapper now measures twice within a step - the block that trains the
    noise floor, and the trailing window an onset is judged on. Both are
    looking at the same audio, so both get the same level.
    """

    delegate = _ScriptedDelegate(states)

    def volume_of(_buffer: bytes, _sample_rate: int) -> float:
        step = max(0, delegate._index - 1)

        return levels[min(step, len(levels) - 1)]

    return AdaptiveVolumeVADAnalyzer(delegate, volume_of=volume_of, **kwargs)


def _analyzer(states: list[VADState], levels: list[float]) -> AdaptiveVolumeVADAnalyzer:
    return _scripted(states, levels)


async def test_a_speech_onset_that_does_not_stand_out_is_suppressed() -> None:
    """
    The reported bug, end to end through the wrapper: the underlying
    analyzer calls the room speech, and the wrapper declines to pass it on.
    """

    # Enough quiet blocks for the window to mean something, then the
    # analyzer reports speech at that same level - which is the room.
    learn = WINDOW_BLOCKS
    states = [VADState.QUIET] * learn + [VADState.SPEAKING] * 3
    levels = [LOUD_ROOM] * learn + [LOUD_ROOM] * 3

    analyzer = _analyzer(states, levels)
    verdicts = await _feed(analyzer, levels)

    assert verdicts[-3:] == [VADState.QUIET] * 3
    assert analyzer.suppressed_runs == 1


async def test_the_caller_speaking_over_the_same_room_is_passed_through() -> None:
    learn = WINDOW_BLOCKS
    states = [VADState.QUIET] * learn + [VADState.SPEAKING] * 3
    levels = [LOUD_ROOM] * learn + [SPEECH] * 3

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

    learn = WINDOW_BLOCKS
    states = [VADState.QUIET] * learn + [VADState.SPEAKING] * 4
    # The caller starts clearly, then trails off to a level that on its own
    # would not have opened the gate.
    levels = [LOUD_ROOM] * learn + [SPEECH, SPEECH, LOUD_ROOM, LOUD_ROOM]

    analyzer = _analyzer(states, levels)
    verdicts = await _feed(analyzer, levels)

    assert verdicts[-4:] == [VADState.SPEAKING] * 4


async def test_a_caller_who_never_pauses_is_still_heard() -> None:
    """
    The minimum estimator's own worst case, pinned so the safety net is
    explicit rather than incidental.

    A caller who talks for a whole window without one quiet block drags the
    floor up towards their own voice. The maximum-threshold clamp is what
    stops that deafening them, and it only works because the clamp sits
    below the level real speech reaches - the mistake caught the first time
    round, when it was set above.
    """

    floor = AdaptiveNoiseFloor()
    settled(floor, SPEECH, blocks=WINDOW_BLOCKS + 10)

    assert floor.floor == pytest.approx(SPEECH, abs=0.001)
    assert floor.admits(SPEECH), (
        "an unbroken monologue raised the floor past the caller's own voice "
        "and the clamp did not catch it"
    )


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


async def test_the_live_failure_replayed() -> None:
    """
    The call that this feature broke, reconstructed from its logs.

    A room at 0.71, a caller at 0.89, and - the detail the original test
    missed - the underlying analyzer reporting QUIET for the first block of
    every utterance, because its start_secs lags the real onset. The first
    implementation folded those full-volume blocks into the background as if
    they were the room. Over the call the floor climbed 0.63, 0.72, 0.76,
    0.80, 0.81, hit its cap, and suppressed the caller for eight minutes.

    The floor must stay on the room, and no caller onset may be suppressed.
    """

    states: list[VADState] = []
    levels: list[float] = []

    for _ in range(WINDOW_BLOCKS + 10):
        states.append(VADState.QUIET)
        levels.append(LOUD_ROOM)

    for _ in range(8):
        # The onset the analyzer has not noticed yet: the caller, at full
        # volume, labelled QUIET.
        for _ in range(ONSET_LAG_BLOCKS):
            states.append(VADState.QUIET)
            levels.append(SPEECH)

        for _ in range(4):
            states.append(VADState.SPEAKING)
            levels.append(SPEECH)

        for _ in range(3):
            states.append(VADState.QUIET)
            levels.append(LOUD_ROOM)

    analyzer = _analyzer(states, levels)
    await _feed(analyzer, levels)

    assert analyzer.noise_floor.floor == pytest.approx(LOUD_ROOM, abs=0.001), (
        f"the floor drifted to {analyzer.noise_floor.floor:.3f} against a "
        f"room at {LOUD_ROOM} - the caller's own onsets are training it"
    )
    # Deliberately not asserting a suppression count: that depends on the
    # margin, which is uncalibrated. What must hold at any margin is that
    # the caller's own onsets did not move the floor.


async def test_shadow_mode_reports_without_acting() -> None:
    """
    How the margin gets calibrated against a deployment's real numbers
    without risking a call on an uncalibrated guess - which is how this went
    wrong the first time.
    """

    learn = WINDOW_BLOCKS
    states = [VADState.QUIET] * learn + [VADState.SPEAKING] * 3
    levels = [LOUD_ROOM] * learn + [LOUD_ROOM] * 3

    analyzer = _scripted(states, levels, shadow=True)
    verdicts = await _feed(analyzer, levels)

    # It would have suppressed these, and says so - but the call is
    # unaffected.
    assert analyzer.suppressed_runs == 1
    assert verdicts[-3:] == [VADState.SPEAKING] * 3


# ---------------------------------------------------------------------------
# The second live failure: the onset judged on the wrong audio, and one wrong
# verdict lasting the rest of the call.
#
# Everything above feeds one complete gating block per analyze_audio call,
# which is the one arrangement in which the old implementation looked
# correct: a block finished on exactly the step the verdict was taken, so
# "the last completed block" and "now" were the same audio. A real analyzer
# is handed ~32ms at a time and a block finishes whenever it finishes, so at
# an onset the last completed block is the room a moment earlier. These tests
# feed small frames so that misalignment is present, which is the only way
# they can see the bug.
# ---------------------------------------------------------------------------

FRAME_SECONDS = 0.02
SAMPLE_RATE = 16000
FRAME_SAMPLES = int(FRAME_SECONDS * SAMPLE_RATE)

# Amplitudes chosen so the measure below reports the levels this file is
# written against: the room at about 0.62, the caller at about 0.82.
ROOM_AMPLITUDE = 130
SPEECH_AMPLITUDE = 1304


def _pcm(amplitude: int, frames: int = 1) -> bytes:
    """
    Frames at a fixed RMS amplitude.

    Alternating sign, so the RMS is the amplitude exactly and no test
    depends on a random seed.
    """

    sample = int(amplitude).to_bytes(2, "little", signed=True)
    flipped = int(-amplitude).to_bytes(2, "little", signed=True)

    return (sample + flipped) * (FRAME_SAMPLES * frames // 2)


def _loudness(buffer: bytes, _sample_rate: int) -> float:
    """
    dBFS mapped onto pipecat's -110..-10 normalized scale.

    A stand-in for calculate_audio_volume on the same scale and in the same
    units, without depending on the exact shape of its filtering - what these
    tests need is that a window half full of speech reads much closer to
    speech than to the room, which is a property of energy, not of BS.1770.
    """

    samples = memoryview(buffer).cast("h")

    if not len(samples):
        return 0.0

    mean_square = sum(sample * sample for sample in samples) / len(samples)
    rms = math.sqrt(mean_square)

    if rms <= 0:
        return 0.0

    decibels = 20 * math.log10(rms / 32768)

    return min(1.0, max(0.0, (decibels + 110.0) / 100.0))


class _Call:
    """
    A call fed frame by frame, with the delegate's verdict and the audio's
    level given independently - because the whole bug lives in the gap
    between when speech starts and when the delegate admits it has.
    """

    def __init__(self, **kwargs) -> None:
        self.states: list[VADState] = []
        self.delegate = _ScriptedDelegate(self.states)
        self.analyzer = AdaptiveVolumeVADAnalyzer(
            self.delegate, volume_of=_loudness, **kwargs
        )
        self.verdicts: list[VADState] = []

    async def feed(self, *, amplitude: int, state: VADState, frames: int) -> None:
        audio = _pcm(amplitude)

        for _ in range(frames):
            self.states.append(state)
            self.verdicts.append(await self.analyzer.analyze_audio(audio))


def _frames(seconds: float) -> int:
    return round(seconds / FRAME_SECONDS)


# Long enough for AdaptiveNoiseFloor.learned, which wants a quarter of the
# window: WINDOW_BLOCKS // 4 gating blocks of 0.4s each.
_LEARNING_SECONDS = (WINDOW_BLOCKS // 4) * 0.4 + 0.4


async def test_the_onset_is_judged_on_the_sound_that_caused_it() -> None:
    """
    The regression for the live failure of 14 September.

    The delegate confirms speech 0.2s after it starts, and at that instant
    the most recently *completed* 400ms block is still entirely the room. The
    old implementation read that block, compared the room against a threshold
    derived from the room, and suppressed the caller - the logs from the call
    show it exactly: a suppression at loudness 0.618 against a floor of
    0.618, which is to say the "speech onset" measured as the quietest block
    in twenty seconds.

    Judged instead on the 400ms ending at the decision - half the room, half
    the caller - the caller is heard.
    """

    call = _Call()

    await call.feed(
        amplitude=ROOM_AMPLITUDE,
        state=VADState.QUIET,
        frames=_frames(_LEARNING_SECONDS),
    )

    # Speech has begun, and the delegate has not caught up yet.
    await call.feed(
        amplitude=SPEECH_AMPLITUDE, state=VADState.QUIET, frames=_frames(0.2)
    )
    await call.feed(
        amplitude=SPEECH_AMPLITUDE, state=VADState.SPEAKING, frames=_frames(0.5)
    )

    assert call.analyzer.suppressed_runs == 0, (
        "the caller's first words were judged against audio from before "
        "they started speaking"
    )
    assert call.verdicts[-1] == VADState.SPEAKING


async def test_the_room_is_still_suppressed_when_it_is_the_room() -> None:
    """
    The other half, and the reason the previous test is not simply "admit
    everything": audio that really is the room, at the room's own level, is
    still refused.
    """

    call = _Call()

    await call.feed(
        amplitude=ROOM_AMPLITUDE,
        state=VADState.QUIET,
        frames=_frames(_LEARNING_SECONDS),
    )
    await call.feed(
        amplitude=ROOM_AMPLITUDE, state=VADState.SPEAKING, frames=_frames(0.5)
    )

    assert call.analyzer.suppressed_runs == 1
    assert call.verdicts[-1] == VADState.QUIET


async def test_a_suppression_cannot_outlast_the_phrase_it_suppressed() -> None:
    """
    The second defect, and the one that turned a clipped phrase into a silent
    call.

    The verdict is taken once per run of SPEAKING and held. Nothing bounded
    the run: the delegate's own volume gate is turned down to 0.3 to defer to
    this one, so a room it calls speech can hold SPEAKING for minutes. On the
    reported call one misjudged onset at 18:07:05 was still muting the caller
    when they hung up five minutes later - every transcript in between
    committed with words=0, because the provider was being sent silence.

    So a suppression expires and the question is asked again. Here the caller
    starts speaking into a run that was already suppressed, and has to be
    heard without the delegate ever having gone quiet.
    """

    call = _Call()

    await call.feed(
        amplitude=ROOM_AMPLITUDE,
        state=VADState.QUIET,
        frames=_frames(_LEARNING_SECONDS),
    )

    # The room trips the delegate, and is correctly suppressed.
    await call.feed(
        amplitude=ROOM_AMPLITUDE, state=VADState.SPEAKING, frames=_frames(0.5)
    )

    assert call.verdicts[-1] == VADState.QUIET

    # The caller now speaks, while the delegate stays SPEAKING throughout.
    before = len(call.verdicts)
    await call.feed(
        amplitude=SPEECH_AMPLITUDE, state=VADState.SPEAKING, frames=_frames(5.0)
    )

    during_speech = call.verdicts[before:]

    assert VADState.SPEAKING in during_speech, (
        "one suppressed onset muted the caller for the rest of the run"
    )

    # And not much later than promised: the expiry, plus the window it then
    # measures, plus a frame.
    heard_after = during_speech.index(VADState.SPEAKING) * FRAME_SECONDS

    assert heard_after <= MAX_SUPPRESSED_RUN_SECONDS + ONSET_WINDOW_SECONDS + 0.1


async def test_an_admitted_caller_is_never_re_judged_mid_phrase() -> None:
    """
    The expiry applies to suppressions only, and this is why.

    A voice falls at the end of a phrase and drops towards the room's level.
    Re-judging an admitted run would cut the caller off on their own
    trailing words - the flapping the wrapper exists not to introduce - so
    once a run is admitted it runs to the delegate's own end.
    """

    call = _Call()

    await call.feed(
        amplitude=ROOM_AMPLITUDE,
        state=VADState.QUIET,
        frames=_frames(_LEARNING_SECONDS),
    )

    # With the delegate's own onset lag, as above. Without it the window at
    # the decision is 95% room and the onset is suppressed - which is the
    # gate working, and makes this test measure nothing.
    await call.feed(
        amplitude=SPEECH_AMPLITUDE, state=VADState.QUIET, frames=_frames(0.2)
    )
    await call.feed(
        amplitude=SPEECH_AMPLITUDE, state=VADState.SPEAKING, frames=_frames(0.5)
    )

    assert call.verdicts[-1] == VADState.SPEAKING, "the onset was not admitted"

    before = len(call.verdicts)

    # The voice falls away, well past the suppression expiry.
    await call.feed(
        amplitude=ROOM_AMPLITUDE, state=VADState.SPEAKING, frames=_frames(5.0)
    )

    assert set(call.verdicts[before:]) == {VADState.SPEAKING}
    assert call.analyzer.suppressed_runs == 0
