"""
A volume floor that follows each call's own background instead of being
guessed in advance.

One absolute threshold cannot separate "this caller" from "that room",
because it is being asked two questions whose right answers depend on the
call. This project has shipped the failure in both directions: set high
enough to ignore a noisy room, it ignored a caller on a quiet microphone and
they heard nothing for an entire call; set low enough to hear them, it
started answering voices in the room behind them. Both were reported as bugs
and both were the same threshold.

The way out is to stop asking for an absolute level and ask for a *relative*
one: speech is what stands out above whatever this particular room sounds
like. A quiet caller in a silent room stands out just as clearly as a loud
one in a noisy cafe, and that is the property a fixed number cannot express.

**This was wrong once, in a way worth recording.** The first version learned
the floor by exponentially smoothing every block the underlying analyzer did
not call speech. That analyzer has a start_secs of 0.2, so it reports QUIET
for the first fifth of a second of every utterance - and those blocks, which
are the caller at full volume, were folded into the background. On a live
call the floor climbed from 0.63 to 0.81 against a measured background of
0.71, hit its own cap, and suppressed the caller for eight minutes. The unit
test that was supposed to prevent exactly this - "the caller's own voice
never trains the gate against them" - passed, because its scripted analyzer
reported speech instantly and so had no onset lag to leak through.

So the floor no longer asks the analyzer what was speech. It takes the
**minimum loudness over a sliding window**, which is the standard way to
estimate a noise floor and is structurally immune to the failure above:
speech is intermittent, people breathe between phrases, and over twelve
seconds the quietest block is the room. Speech cannot be the minimum, so it
cannot raise the floor no matter how the analyzer labels it.

One design point survives from the first attempt:

- **Only the onset is gated.** The underlying analyzer has hysteresis -
  start_secs and stop_secs - and second-guessing it block by block would
  turn a steady "speaking" into a flapping one. So this decides whether a
  turn of speech may *begin*, and once it has, the analyzer runs it to its
  end unmodified.

**And it was wrong a second time, differently.** Rebuilding the estimator
fixed how the floor is learned and left two faults in how the onset is
*judged*, which together muted a caller for most of a five-minute call:

- **The onset was judged on audio from before it.** Loudness was read from
  the last *completed* 400ms gating block, so at the moment the delegate
  confirmed speech the reading described the room a fraction of a second
  earlier - not the speech. The log said so plainly and was not read
  carefully enough: two suppressions at loudness 0.605 and 0.618 against
  floors of 0.559 and 0.618, where real speech onsets on the same machine
  measure 0.78 to 0.845. The second is the giveaway - a "speech onset"
  measured at exactly the quietest block in twenty seconds is not speech
  being measured. The onset is now measured over the audio immediately
  *preceding the decision*, which is the audio that made the delegate say
  speech.

- **A single wrong verdict latched.** The verdict is taken once per run of
  SPEAKING and held, which is right, but nothing bounded how long a run
  lasts - and with the delegate's own volume gate turned down to defer to
  this one, a noisy room can hold it SPEAKING indefinitely. One misjudged
  onset therefore muted the caller not for a phrase but until the room
  went quiet. A suppression now expires: after MAX_SUPPRESSED_RUN_SECONDS
  the decision is taken again. Deciding afresh every couple of seconds is
  not the block-by-block flapping the paragraph above rules out; it is the
  bound that made that latch safe to have in the first place.
"""

import logging
from collections import deque
from collections.abc import Callable

from pipecat.audio.utils import calculate_audio_volume
from pipecat.audio.vad.vad_analyzer import VADAnalyzer, VADState

logger = logging.getLogger(__name__)

# BS.1770 needs a full gating block to measure at all, and 400ms is its
# definition of one. It is also about the right responsiveness for a noise
# floor: a room's level changes over seconds, not milliseconds.
GATING_BLOCK_SECONDS = 0.4

# How far above the measured floor speech must sit, in normalized-loudness
# units.
#
# Provisional, and derived from one call's logged numbers rather than chosen:
# quiet stretches measured around 0.615, the room's louder moments 0.71, and
# the caller's own onsets 0.78 to 0.845. A margin of 0.10 over a floor near
# 0.615 puts the line at about 0.715 - above the room, below the caller.
#
# That is a window of well under 0.10 between "room" and "caller", which is
# uncomfortably tight and says something the gate cannot fix: on this
# microphone the room and the person using it arrive at nearly the same
# loudness. Capture is the better lever than any threshold when that is true.
#
# Calibrate before trusting it. VAD_ADAPTIVE_FLOOR_SHADOW logs what this
# would decide without acting on it, which is how to collect a real
# distribution without risking a call - the mistake that made this necessary.
DEFAULT_MARGIN = 0.10

# Twenty seconds of 400ms blocks.
#
# The length is a trade. Long enough that an ordinary caller pauses at least
# once inside it - a single quiet 400ms block anywhere in twenty seconds is
# enough, and people breathe - which is what makes the minimum the room
# rather than the caller. Short enough to follow a room that genuinely
# changes within about the same time.
#
# The failure mode if it is ever too short: a caller who talks for the whole
# window without one quiet block drags the floor up towards their own voice.
# DEFAULT_MAX_THRESHOLD bounds the damage - the threshold cannot climb past a
# level real speech clears - which is why that cap sits below measured speech
# rather than above it. There is a test for exactly this.
WINDOW_BLOCKS = 50

# The line can move, but not anywhere. Below the minimum, a silent room would
# drop the threshold far enough to admit anything audible.
DEFAULT_MIN_THRESHOLD = 0.55

# The cap has to sit below the quietest real speech to be a safety net rather
# than a trap: past it, the threshold would be a level the caller can never
# clear, which is the deafness this exists to prevent.
#
# It has now been wrong twice, in the same direction, for the same reason -
# a number taken from synthesised audio rather than a call. 0.90 came from
# speech-shaped noise at the caller's p90 peak; 0.85 from the same source.
# Measured onsets on a real call are 0.78 to 0.845, so both sat at or above
# real speech. 0.75 is below the quietest of them.
#
# Past this point the room is louder than the caller and no threshold can
# separate them. That is a microphone problem, and the gate degrading to
# "admit things" is the right way to lose - the caller is heard, some of the
# room is too, and someone can hear that something is wrong.
DEFAULT_MAX_THRESHOLD = 0.75

# How much of the audio immediately before a decision is measured to judge
# that decision.
#
# A full gating block, ending *now* rather than whenever the last one
# happened to finish. The delegate confirms speech start_secs (0.2) after it
# begins, so this window is roughly half speech and half the room before it -
# and loudness is an energy measure, so speech twenty decibels above the room
# still dominates a window it only half fills. Reading a stale block instead
# is what muted a live call.
ONSET_WINDOW_SECONDS = 0.4

# The longest one "this is the room" verdict may stand before it is taken
# again.
#
# Short enough that the worst case of a wrong verdict is a clipped phrase
# rather than a silent call, long enough not to reopen mid-phrase on a room
# that really is being suppressed. The failure it bounds is not theoretical:
# without it, one misjudged onset held for over five minutes.
MAX_SUPPRESSED_RUN_SECONDS = 2.0


class AdaptiveNoiseFloor:
    """
    The measured level of one call's background, and the threshold that
    follows from it.

    Deliberately pure: no audio, no analyzer, no clock, and - the point of
    the rewrite - no opinion from anything else about what was speech. It
    takes loudness readings and reports the quietest recent one.
    """

    def __init__(
        self,
        *,
        margin: float = DEFAULT_MARGIN,
        window_blocks: int = WINDOW_BLOCKS,
        minimum: float = DEFAULT_MIN_THRESHOLD,
        maximum: float = DEFAULT_MAX_THRESHOLD,
    ) -> None:
        self._margin = margin
        self._window_blocks = window_blocks
        self._minimum = minimum
        self._maximum = maximum
        self._window: deque[float] = deque(maxlen=window_blocks)

    @property
    def learned(self) -> bool:
        """
        Whether enough of the window has filled for the minimum to mean
        anything.

        Until it has, `threshold` is the configured minimum: the opening
        seconds of a call are the worst possible time to be deaf, and being
        permissive there errs towards hearing the caller's first words.
        """

        return len(self._window) >= self._window_blocks // 4

    @property
    def floor(self) -> float:
        return min(self._window) if self._window else 0.0

    @property
    def threshold(self) -> float:
        if not self.learned:
            return self._minimum

        return min(self._maximum, max(self._minimum, self.floor + self._margin))

    def observe(self, loudness: float) -> None:
        """
        Record one block's loudness, whatever it was.

        Every block, speech included, and that is safe precisely because the
        estimate is a minimum: the caller's own voice can only ever be
        larger than the quietest block in the window, so it cannot move the
        floor. The previous implementation asked the analyzer which blocks
        were speech and excluded those, which is what let the analyzer's
        onset lag leak speech into the floor and deafen a call.
        """

        self._window.append(loudness)

    def admits(self, loudness: float) -> bool:
        return loudness >= self.threshold


class AdaptiveVolumeVADAnalyzer(VADAnalyzer):
    """
    Wraps a real VAD analyzer and suppresses speech onsets that do not stand
    out above this call's own background.

    Wrapping rather than subclassing Silero: the point is to be indifferent
    to which analyzer is underneath, and pipecat's own `set_params` resets
    the analyzer's state machine, so moving its threshold on the fly would
    knock it out of SPEAKING mid-sentence. The underlying analyzer keeps a
    permissive absolute floor and this decides what may start a turn.
    """

    def __init__(
        self,
        delegate: VADAnalyzer,
        *,
        noise_floor: AdaptiveNoiseFloor | None = None,
        volume_of: Callable[[bytes, int], float] = calculate_audio_volume,
        shadow: bool = False,
        onset_window_seconds: float = ONSET_WINDOW_SECONDS,
        max_suppressed_run_seconds: float = MAX_SUPPRESSED_RUN_SECONDS,
    ) -> None:
        self._delegate = delegate
        self._noise_floor = noise_floor or AdaptiveNoiseFloor()
        self._volume_of = volume_of
        self._onset_window_seconds = onset_window_seconds
        self._max_suppressed_run_seconds = max_suppressed_run_seconds
        # Shadow mode logs what this would have decided and then passes the
        # delegate's verdict through untouched. It exists because the first
        # version of this gate deafened a live call, and the margin it needs
        # has to be calibrated against a deployment's own numbers - which
        # should not require risking another call to collect.
        self._shadow = shadow
        self._block: bytearray = bytearray()
        # The audio immediately behind the playhead, kept so an onset can be
        # judged on the sound that caused it rather than on the last block
        # that happened to finish.
        self._onset_window: bytearray = bytearray()
        self._recent_loudness = 0.0
        # Samples of SPEAKING seen since the current suppression was decided.
        self._suppressed_samples = 0
        # Whether the delegate's current run of SPEAKING was admitted. None
        # while it is quiet; True or False for the duration of a run, so the
        # verdict is taken once at the onset and then held - see the module
        # docstring on not second-guessing hysteresis.
        self._admitted: bool | None = None
        self._suppressed_runs = 0

    # -- VADAnalyzer's own surface, forwarded ---------------------------

    @property
    def params(self):
        return self._delegate.params

    def set_params(self, params) -> None:
        self._delegate.set_params(params)

    def set_sample_rate(self, sample_rate: int) -> None:
        self._delegate.set_sample_rate(sample_rate)

    @property
    def sample_rate(self) -> int:
        return self._delegate.sample_rate

    def num_frames_required(self) -> int:
        return self._delegate.num_frames_required()

    def voice_confidence(self, buffer: bytes) -> float:
        return self._delegate.voice_confidence(buffer)

    async def cleanup(self) -> None:
        await self._delegate.cleanup()

    # -- the actual behaviour ------------------------------------------

    @property
    def noise_floor(self) -> AdaptiveNoiseFloor:
        return self._noise_floor

    @property
    def suppressed_runs(self) -> int:
        """How many speech onsets have been rejected as the room, this call."""

        return self._suppressed_runs

    async def analyze_audio(self, buffer: bytes) -> VADState:
        state = await self._delegate.analyze_audio(buffer)

        self._remember(buffer)
        self._measure(buffer)

        if state != VADState.SPEAKING:
            # The run is over; the next onset gets a fresh decision.
            self._admitted = None
            self._suppressed_samples = 0

            return state

        if self._admitted is None:
            self._decide()
        elif not self._admitted:
            # A suppression is the only verdict with an expiry. Admitting is
            # allowed to stand for the whole run - that is the hysteresis
            # this deliberately does not second-guess - but muting the caller
            # has to be reconsidered, because getting it wrong is the failure
            # that matters and nothing else will notice.
            self._suppressed_samples += len(buffer) // 2
            limit = self._max_suppressed_run_seconds * max(1, self.sample_rate)

            if self._suppressed_samples >= limit:
                self._suppressed_samples = 0
                self._decide()

        if self._shadow:
            return state

        return VADState.SPEAKING if self._admitted else VADState.QUIET

    def _decide(self) -> None:
        """Take the admit/suppress verdict for the run, and say why."""

        loudness = self._onset_loudness()
        self._admitted = self._noise_floor.admits(loudness)

        if not self._admitted:
            self._suppressed_runs += 1
            # Levels only, never anything transcribed (CLAUDE.md 27).
            logger.info(
                "vad onset %s as background: loudness=%.3f floor=%.3f "
                "threshold=%.3f",
                "would be suppressed" if self._shadow else "suppressed",
                loudness,
                self._noise_floor.floor,
                self._noise_floor.threshold,
            )
        elif self._shadow:
            logger.info(
                "vad onset would be admitted: loudness=%.3f floor=%.3f "
                "threshold=%.3f",
                loudness,
                self._noise_floor.floor,
                self._noise_floor.threshold,
            )

    def _onset_loudness(self) -> float:
        """
        The loudness of the audio immediately before now.

        Falls back to the last completed gating block only while too little
        audio has arrived to fill the window - the opening moments of a call,
        where being permissive is the right way to be wrong.
        """

        wanted = self._window_bytes(self._onset_window_seconds)

        if wanted <= 0 or len(self._onset_window) < wanted:
            return self._recent_loudness

        try:
            return self._volume_of(bytes(self._onset_window), self.sample_rate)
        except Exception:  # pragma: no cover - defensive
            logger.debug("could not measure the onset window", exc_info=True)

            return self._recent_loudness

    def _window_bytes(self, seconds: float) -> int:
        return int(seconds * self.sample_rate) * 2

    def _remember(self, buffer: bytes) -> None:
        """Keep the trailing onset window, and no more than that."""

        wanted = self._window_bytes(self._onset_window_seconds)

        if wanted <= 0:
            return

        self._onset_window.extend(buffer)

        if len(self._onset_window) > wanted:
            del self._onset_window[: len(self._onset_window) - wanted]

    def _measure(self, buffer: bytes) -> None:
        """
        Accumulate a gating block, and once there is one, measure it.

        Every block goes into the floor, speech included. That is safe
        because the floor is a minimum over a window: the caller's voice is
        never the quietest block in twelve seconds, so it cannot raise the
        bar against them. Filtering by the analyzer's verdict is what broke
        the first version - see the module docstring.
        """

        self._block.extend(buffer)

        block_bytes = int(GATING_BLOCK_SECONDS * self.sample_rate) * 2

        if block_bytes <= 0 or len(self._block) < block_bytes:
            return

        measured = bytes(self._block[:block_bytes])
        del self._block[:block_bytes]

        try:
            loudness = self._volume_of(measured, self.sample_rate)
        except Exception:  # pragma: no cover - defensive
            # An unmeasurable block must not take the call down with it; the
            # previous reading stands and the next block tries again.
            logger.debug("could not measure a block's loudness", exc_info=True)

            return

        self._recent_loudness = loudness
        self._noise_floor.observe(loudness)
