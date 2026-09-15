"""
What the voice-activity detector is actually seeing.

"I said hello but there is no transcript of me and also no response" has now
been diagnosed twice from the outside - once by reading peak levels out of
the level meter, once by mapping those peaks onto a loudness table - and the
fix was wrong both times, because neither number is the one the detector
compares. pipecat decides:

    speaking = confidence >= params.confidence AND volume >= params.min_volume

Two gates, either of which can silently veto the caller, and a `volume` that
is not a block's loudness but an *exponentially smoothed* running value - so
a brief "hello" in a stream that is otherwise near-silent starts from the
background's number and may never climb to the threshold at all, however
loud the word itself was.

From outside, all three failures look identical: no speech, no transcript,
no answer. This logs the two numbers side by side so the next call says
which gate is shut, instead of another round of inference from peaks.

Deliberately cheap and deliberately quiet: one summary line per window, at
the same cadence as the existing level meter, carrying maxima rather than a
line per frame. It records levels and a confidence score - never audio,
never anything transcribed (CLAUDE.md section 27).
"""

import logging
import time

from pipecat.audio.utils import calculate_audio_volume
from pipecat.audio.vad.vad_analyzer import VADAnalyzer, VADState

logger = logging.getLogger(__name__)

# Matches the caller-audio level meter, so the two lines interleave and one
# window can be read against the other.
REPORT_EVERY_SECONDS = 2.0


class DiagnosticVADAnalyzer(VADAnalyzer):
    """
    Passes every verdict through untouched and reports what produced it.

    Wrapping rather than subclassing Silero, for the same reason
    AdaptiveVolumeVADAnalyzer does: this has to be able to sit over whatever
    analyzer is configured, including that one.

    The volume reported here is the raw loudness of the buffer, not
    pipecat's smoothed running value - that one is private and stateful, and
    calling it from here would advance its state and corrupt the very
    decision being measured. Raw is enough to place the blame: if
    confidence never reaches its threshold, the volume floor is irrelevant;
    if confidence clears it and raw loudness clears the floor while the
    detector still says nothing, the smoothing is what swallowed it.
    """

    def __init__(self, delegate: VADAnalyzer) -> None:
        self._delegate = delegate
        self._window_started = time.monotonic()
        self._max_confidence = 0.0
        self._max_volume = 0.0
        self._blocks = 0
        self._speaking_blocks = 0

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

        self._observe(buffer, state)

        return state

    def _observe(self, buffer: bytes, state: VADState) -> None:
        try:
            confidence = self._delegate.voice_confidence(buffer)
            volume = calculate_audio_volume(buffer, self.sample_rate)
        except Exception:  # pragma: no cover - defensive
            # A measurement must never be able to take a call down with it.
            logger.debug("could not measure a VAD block", exc_info=True)

            return

        self._blocks += 1
        self._max_confidence = max(self._max_confidence, confidence)
        self._max_volume = max(self._max_volume, volume)

        if state == VADState.SPEAKING:
            self._speaking_blocks += 1

        now = time.monotonic()

        if now - self._window_started < REPORT_EVERY_SECONDS:
            return

        params = self._delegate.params
        wanted_confidence = getattr(params, "confidence", None)
        wanted_volume = getattr(params, "min_volume", None)

        # Which gate was shut, in the window's best block. Both have to open
        # for the caller to be heard, so naming the one that did not is the
        # whole point of the line.
        if wanted_confidence is not None and self._max_confidence < wanted_confidence:
            verdict = "confidence too low"
        elif wanted_volume is not None and self._max_volume < wanted_volume:
            verdict = "volume too low"
        elif self._speaking_blocks:
            verdict = "speech"
        else:
            verdict = "both cleared, smoothing or hysteresis held it"

        logger.info(
            "vad window: best confidence=%.3f (needs %.2f) best volume=%.3f "
            "(needs %.2f) speaking_blocks=%d/%d -> %s",
            self._max_confidence,
            wanted_confidence if wanted_confidence is not None else -1.0,
            self._max_volume,
            wanted_volume if wanted_volume is not None else -1.0,
            self._speaking_blocks,
            self._blocks,
            verdict,
        )

        self._window_started = now
        self._max_confidence = 0.0
        self._max_volume = 0.0
        self._blocks = 0
        self._speaking_blocks = 0
