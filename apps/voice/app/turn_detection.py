"""
Pure turn-detection decision logic (item 20c) - VAD-detected sustained
silence plus a placeholder semantic-completeness check on the latest final
transcript, with a hard fallback timeout so an incomplete-sounding sentence
never leaves a caller in dead air forever. Deliberately no Pipecat or
WebSocket dependency here, mirroring items 17-19's chunker.py/
context_builder.py precedent - app/media_session.py is the thin adapter
that wires this into the live pipeline.
"""

import time
from collections.abc import Callable

from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADAnalyzer, VADParams, VADState

# VADParams.stop_secs at the two ends of AssistantVersion.turn_sensitivity's
# existing 0.0-1.0 range (item 11b) - 0.0 (most patient) waits nearly a
# second and a half of silence before VAD itself reports quiet again, 1.0
# (most eager) reports it after well under half a second. Starting values,
# not tuned against real call data - see this feature's spec for why that
# tuning is explicitly out of scope here.
_MIN_STOP_SECS = 0.3
_MAX_STOP_SECS = 1.5

# How long sustained silence may persist with a semantically-incomplete
# transcript before the turn ends anyway. Roughly double the most patient
# stop_secs above - real extra grace, without ever approaching a duration a
# caller would perceive as a hang (CLAUDE.md's "silence is the worst
# possible failure").
FALLBACK_TIMEOUT_SECONDS = 3.0

# A deliberate placeholder, not real semantic modeling - see this feature's
# spec for why building or hosting a real classifier is out of scope here.
_CONTINUATION_WORDS = frozenset({"and", "but", "so", "or", "because", "um", "uh"})
_TERMINAL_PUNCTUATION = (".", "!", "?")


def sensitivity_to_stop_secs(sensitivity: float) -> float:
    """
    Map AssistantVersion.turn_sensitivity (0.0-1.0) onto VADParams.stop_secs.
    Higher sensitivity means less patience for silence, so it maps to a
    shorter stop_secs.
    """

    clamped = max(0.0, min(1.0, sensitivity))

    return _MAX_STOP_SECS - clamped * (_MAX_STOP_SECS - _MIN_STOP_SECS)


def is_semantically_complete(text: str) -> bool:
    """
    Placeholder semantic-completeness heuristic: does the text end in
    terminal punctuation, and does it not trail off on an obvious
    continuation word? Empty text is never complete - there is nothing to
    end a turn on.
    """

    stripped = text.strip()

    if not stripped:
        return False

    last_word = stripped.rstrip("".join(_TERMINAL_PUNCTUATION)).split()[-1:]

    if last_word and last_word[0].lower() in _CONTINUATION_WORDS:
        return False

    return stripped.endswith(_TERMINAL_PUNCTUATION)


def _build_default_vad_analyzer(*, sensitivity: float, sample_rate: int) -> VADAnalyzer:
    analyzer = SileroVADAnalyzer(params=VADParams(stop_secs=sensitivity_to_stop_secs(sensitivity)))

    # The constructor's sample_rate kwarg alone does not take effect - the
    # analyzer's active sample rate stays 0, and stop_secs/start_secs never
    # get converted to frame counts, until set_sample_rate() actually runs.
    # Confirmed empirically while building this feature.
    analyzer.set_sample_rate(sample_rate)

    return analyzer


class TurnDetector:
    """
    Feed it raw audio and transcript events as they arrive; ask it
    turn_ended() to find out whether the caller's turn is over.

    vad_analyzer defaults to a real SileroVADAnalyzer but accepts any object
    exposing set_sample_rate(int) and async analyze_audio(bytes) -> VADState
    - tests inject a scripted fake so the real model is never loaded there.
    """

    def __init__(
        self,
        *,
        sensitivity: float,
        sample_rate: int,
        vad_analyzer: VADAnalyzer | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._vad_analyzer = vad_analyzer or _build_default_vad_analyzer(
            sensitivity=sensitivity, sample_rate=sample_rate
        )
        self._clock = clock

        self._ever_spoken = False
        self._silence_since: float | None = None
        self._last_final_transcript = ""
        self._turn_ended = False

    async def feed_audio(self, chunk: bytes) -> None:
        if self._turn_ended:
            return

        state = await self._vad_analyzer.analyze_audio(chunk)

        if state == VADState.SPEAKING:
            self._ever_spoken = True
            self._silence_since = None
        elif state == VADState.QUIET and self._ever_spoken and self._silence_since is None:
            self._silence_since = self._clock()

        self._recompute()

    def feed_transcript(self, text: str, *, is_final: bool) -> None:
        if is_final:
            self._last_final_transcript = text

        self._recompute()

    def turn_ended(self) -> bool:
        return self._turn_ended

    @property
    def last_final_transcript(self) -> str:
        return self._last_final_transcript

    def _recompute(self) -> None:
        if self._turn_ended or self._silence_since is None:
            return

        if is_semantically_complete(self._last_final_transcript):
            self._turn_ended = True
            return

        if self._clock() - self._silence_since >= FALLBACK_TIMEOUT_SECONDS:
            self._turn_ended = True
