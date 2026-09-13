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

Two design points worth stating, both taken from measurements of real calls
rather than from theory:

- **The floor tracks the loud part of the background, not its average.**
  Measured here, the background's median two-second peak was 448 (-37 dBFS)
  but its p75 was 1501 (-27 dBFS), and normalized loudness compresses those
  to 0.51 and 0.71. A floor sitting at the average would be cleared by the
  room's own louder moments, which is precisely what has to be rejected. The
  browser-side echo gate already peak-tracks for the same reason.

- **Only the onset is gated.** The underlying analyzer has hysteresis -
  start_secs and stop_secs - and second-guessing it frame by frame would
  turn a steady "speaking" into a flapping one. So this decides whether a
  turn of speech may *begin*, and once it has, the analyzer runs it to its
  end unmodified.
"""

import logging
from collections.abc import Callable

from pipecat.audio.utils import calculate_audio_volume
from pipecat.audio.vad.vad_analyzer import VADAnalyzer, VADState

logger = logging.getLogger(__name__)

# BS.1770 needs a full gating block to measure at all, and 400ms is its
# definition of one. It is also about the right responsiveness for a noise
# floor: a room's level changes over seconds, not milliseconds.
GATING_BLOCK_SECONDS = 0.4

# How far above the room speech must sit, in normalized-loudness units.
#
# From the measured distribution: background reaches about 0.71 at its
# loudest and speech sits at about 0.89, so 0.10 puts the line at 0.81 -
# clear of the room, comfortably under the caller. In a quiet room the floor
# falls to about 0.51 and the line with it, to 0.61, which is what lets a
# softly-spoken caller be heard there without also admitting a noisy room
# elsewhere.
DEFAULT_MARGIN = 0.10

# The line can move, but not anywhere. Below the minimum, a silent room would
# drop the threshold far enough to admit anything audible; above the maximum,
# a very loud room would push it past reachable speech and deafen the
# assistant completely - the failure this whole module exists to prevent.
DEFAULT_MIN_THRESHOLD = 0.55

# 0.85, not 0.90. The first value chosen was 0.90, which a smoke test showed
# sits *above* the 0.89 that speech measures here - so a room loud enough to
# push the floor that high would have clamped the threshold to a level the
# caller could never clear, reintroducing the deafness this replaces. The cap
# has to sit below realistic speech to be a safety net rather than a trap:
# 0.85 is clear of the loudest measured background (0.71) and under measured
# speech (0.89).
#
# Past this point the room is louder than the caller and no threshold can
# separate them. That is a microphone problem, and the gate degrading to
# "admit things" is the right way to lose - the caller is heard, some of the
# room is too, and someone can hear that something is wrong.
DEFAULT_MAX_THRESHOLD = 0.85

# How fast the floor follows the room. Rising quickly and falling slowly is
# deliberate and asymmetric: a new noise source (an air conditioner, a
# television) must be accommodated within a few seconds or it is answered,
# while a room going quiet is not urgent - a threshold that stays slightly
# too high for a few seconds costs nothing, because the caller's speech is
# well above it either way.
RISE = 0.35
FALL = 0.02


class AdaptiveNoiseFloor:
    """
    The learned level of one call's background, and the threshold that
    follows from it.

    Deliberately pure: no audio, no analyzer, no clock. What it does is a
    handful of arithmetic decisions that have to be right, and they are far
    easier to pin down as numbers in a test than as sound.
    """

    def __init__(
        self,
        *,
        margin: float = DEFAULT_MARGIN,
        minimum: float = DEFAULT_MIN_THRESHOLD,
        maximum: float = DEFAULT_MAX_THRESHOLD,
    ) -> None:
        self._margin = margin
        self._minimum = minimum
        self._maximum = maximum
        self._floor: float | None = None

    @property
    def learned(self) -> bool:
        """
        Whether any background has been measured yet.

        Until it has, `threshold` is the configured minimum: the opening
        moments of a call are the worst possible time to be deaf, and a
        permissive threshold there errs towards hearing the caller's first
        words rather than towards silence.
        """

        return self._floor is not None

    @property
    def floor(self) -> float:
        return self._floor if self._floor is not None else 0.0

    @property
    def threshold(self) -> float:
        if self._floor is None:
            return self._minimum

        return min(self._maximum, max(self._minimum, self._floor + self._margin))

    def observe_background(self, loudness: float) -> None:
        """
        Fold one block of not-speech audio into the floor.

        Only ever called for audio the analyzer did not call speech, so the
        caller's own voice cannot raise the bar against them - the failure
        mode of a naive "track everything" implementation, where a talkative
        caller trains the gate to ignore themselves.
        """

        if self._floor is None:
            self._floor = loudness

            return

        rate = RISE if loudness > self._floor else FALL
        self._floor = self._floor * (1 - rate) + loudness * rate

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
    ) -> None:
        self._delegate = delegate
        self._noise_floor = noise_floor or AdaptiveNoiseFloor()
        self._volume_of = volume_of
        self._block: bytearray = bytearray()
        self._recent_loudness = 0.0
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

        self._measure(buffer, state)

        if state != VADState.SPEAKING:
            # The run is over; the next onset gets a fresh decision.
            self._admitted = None

            return state

        if self._admitted is None:
            self._admitted = self._noise_floor.admits(self._recent_loudness)

            if not self._admitted:
                self._suppressed_runs += 1
                # Levels only, never anything transcribed (CLAUDE.md 27).
                logger.info(
                    "vad onset suppressed as background: loudness=%.3f "
                    "floor=%.3f threshold=%.3f",
                    self._recent_loudness,
                    self._noise_floor.floor,
                    self._noise_floor.threshold,
                )

        return VADState.SPEAKING if self._admitted else VADState.QUIET

    def _measure(self, buffer: bytes, state: VADState) -> None:
        """
        Accumulate a gating block, and once there is one, measure it.

        Blocks the analyzer called speech update `_recent_loudness` - what an
        onset is judged against - but are kept out of the floor, so the
        caller never trains the gate against themselves.
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

        if state != VADState.SPEAKING:
            self._noise_floor.observe_background(loudness)
