"""
What the voice-activity detector is actually seeing.

"I said hello but there is no transcript of me and also no response" has been
diagnosed from outside the detector twice and fixed wrongly twice, because
pipecat decides speech with two gates, not one:

    speaking = confidence >= params.confidence AND volume >= params.min_volume

Either can veto the caller on its own, and from outside the failures are
indistinguishable - no speech, no transcript, no answer. Peak level, which is
what the caller-audio meter reports and what both previous diagnoses were
reasoned from, is neither of those quantities.

So this logs the two numbers side by side, each against the threshold it has
to clear, and names the one that fell short.

**Both measurements need more audio than one frame carries, and that is not
a detail.** `TurnDetector.feed_audio` delivers 20ms frames. Loudness needs a
complete 400ms BS.1770 gating block and *raises* below that; Silero needs
exactly its own frame size and quietly returns 0 for anything shorter. The
first version of this file measured single frames, so every block raised, the
exception was swallowed at DEBUG, and it logged nothing at all for a whole
call - an instrument that looked installed and healthy while measuring
nothing, whose silence was then read as evidence. Hence the accumulators
below, and hence _complain: a measurement that cannot be taken says so at
WARNING rather than going quiet.

Levels and a score only - never audio, never anything transcribed
(CLAUDE.md section 27).
"""

import logging
import time

from pipecat.audio.utils import calculate_audio_volume
from pipecat.audio.vad.vad_analyzer import VADAnalyzer, VADState

logger = logging.getLogger(__name__)

# Matches the caller-audio level meter, so the two lines interleave and one
# window can be read against the other.
REPORT_EVERY_SECONDS = 2.0

# A full BS.1770 gating block. calculate_audio_volume raises below this.
VOLUME_BLOCK_SECONDS = 0.4


class DiagnosticVADAnalyzer(VADAnalyzer):
    """
    Passes every verdict through untouched and reports what produced it.

    Wrapping rather than subclassing Silero, for the same reason
    AdaptiveVolumeVADAnalyzer does: it has to sit over whatever analyzer is
    configured, including that one.

    The volume here is the raw loudness of a gating block, not pipecat's
    smoothed running value - that one is private and stateful, and calling it
    from here would advance its state and corrupt the decision being
    measured. Raw is enough to assign blame: if confidence never reaches its
    threshold the volume floor is irrelevant, and if both clear while the
    detector still reports nothing, the smoothing or the start/stop
    hysteresis is what swallowed it, which the line says.
    """

    def __init__(self, delegate: VADAnalyzer) -> None:
        self._delegate = delegate
        self._window_started = time.monotonic()
        self._max_confidence = 0.0
        self._max_volume = 0.0
        self._blocks = 0
        self._speaking_frames = 0
        self._frames = 0
        # Each measurement needs a different amount of audio, so each gets
        # its own accumulator.
        self._volume_block = bytearray()
        self._confidence_block = bytearray()
        self._complained: set[str] = set()

    # -- VADAnalyzer's surface, forwarded --------------------------------

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

    # -- the measurement -------------------------------------------------

    async def analyze_audio(self, buffer: bytes) -> VADState:
        state = await self._delegate.analyze_audio(buffer)

        try:
            self._observe(buffer, state)
        except Exception:  # pragma: no cover - defensive
            # Measuring a call must never be able to end one.
            self._complain("the VAD window")

        return state

    def _observe(self, buffer: bytes, state: VADState) -> None:
        self._frames += 1

        if state == VADState.SPEAKING:
            self._speaking_frames += 1

        self._measure_confidence(buffer)
        self._measure_volume(buffer)
        self._maybe_report()

    def _measure_confidence(self, buffer: bytes) -> None:
        self._confidence_block.extend(buffer)
        wanted = self.num_frames_required() * 2

        if wanted <= 0:
            return

        while len(self._confidence_block) >= wanted:
            chunk = bytes(self._confidence_block[:wanted])
            del self._confidence_block[:wanted]

            try:
                # Silero returns a one-element numpy array, not a float, and
                # %-formatting one raises - which is how the first version of
                # this logged nothing but a logging error.
                confidence = float(self._delegate.voice_confidence(chunk))
            except Exception:
                self._complain("confidence")

                return

            self._max_confidence = max(self._max_confidence, confidence)

    def _measure_volume(self, buffer: bytes) -> None:
        self._volume_block.extend(buffer)
        wanted = int(VOLUME_BLOCK_SECONDS * self.sample_rate) * 2

        if wanted <= 0:
            return

        while len(self._volume_block) >= wanted:
            chunk = bytes(self._volume_block[:wanted])
            del self._volume_block[:wanted]

            try:
                volume = calculate_audio_volume(chunk, self.sample_rate)
            except Exception:
                self._complain("volume")

                return

            self._max_volume = max(self._max_volume, float(volume))
            self._blocks += 1

    def _maybe_report(self) -> None:
        now = time.monotonic()

        if now - self._window_started < REPORT_EVERY_SECONDS:
            return

        params = self._delegate.params
        wants_confidence = getattr(params, "confidence", 0.0)
        wants_volume = getattr(params, "min_volume", 0.0)

        # Which gate was shut in the window's best block. Both have to open
        # for the caller to be heard, so naming the one that did not is the
        # whole point of the line.
        if self._speaking_frames:
            verdict = "speech"
        elif not self._blocks:
            verdict = "no complete block measured yet"
        elif self._max_confidence < wants_confidence:
            verdict = "confidence too low"
        elif self._max_volume < wants_volume:
            verdict = "volume too low"
        else:
            verdict = "both cleared - smoothing or hysteresis held it"

        logger.info(
            "vad window: best confidence=%.3f (needs %.2f) best volume=%.3f "
            "(needs %.2f) speaking=%d/%d frames -> %s",
            self._max_confidence,
            wants_confidence,
            self._max_volume,
            wants_volume,
            self._speaking_frames,
            self._frames,
            verdict,
        )

        self._window_started = now
        self._max_confidence = 0.0
        self._max_volume = 0.0
        self._blocks = 0
        self._speaking_frames = 0
        self._frames = 0

    def _complain(self, which: str) -> None:
        """
        Say once per kind, at WARNING, that a measurement cannot be taken.

        A diagnostic that fails quietly is worse than none, because its
        silence reads as evidence of absence. This one failed on every frame
        of a real call and was believed.
        """

        if which in self._complained:
            return

        self._complained.add(which)
        logger.warning("vad diagnostics cannot measure %s", which, exc_info=True)
