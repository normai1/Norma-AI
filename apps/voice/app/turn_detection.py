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
        # The transcript actively accumulating toward the *next* completed
        # turn - fed by feed_transcript, consumed (and cleared) by
        # _recompute() the moment it decides a turn has ended.
        self._pending_transcript = ""
        # A stable snapshot of whichever transcript most recently ended a
        # turn - what last_final_transcript exposes. Deliberately a
        # separate field from _pending_transcript: see last_final_transcript
        # and reset_for_next_turn for why conflating the two caused a real,
        # hard-to-find bug.
        self._ended_turn_text = ""
        self._turn_ended = False
        self._is_speaking = False

    async def feed_audio(self, chunk: bytes) -> None:
        """
        Always updates VAD-derived state - is_speaking, ever_spoken,
        silence_since - regardless of turn_ended()'s latch. Item 20e's
        barge-in needs this: the eventual reset is delivered asynchronously
        (a caller_speech_started message reacted to by a downstream
        processor, not synchronously alongside the audio frame that caused
        it), so by the time it runs, more audio may already have been fed -
        e.g. the caller speaks once to interrupt, then goes quiet again,
        all before TTSProcessor gets around to resetting. If ever_spoken/
        silence_since only updated while not-yet-latched, that entire
        exchange would be silently lost - reset_for_next_turn() would find
        nothing to seed from, and turn_ended() could never fire again for
        the caller's actual new turn. _recompute()'s own early return on
        turn_ended() already being True is what prevents this from
        prematurely re-flipping the turn that already ended - not a guard
        here - and _recompute() clears ever_spoken/silence_since itself at
        the exact moment it sets turn_ended True, so anything that happens
        during the latch starts from a clean slate rather than carrying
        over the just-finished turn's own stale values.
        """

        state = await self._vad_analyzer.analyze_audio(chunk)
        self._is_speaking = state == VADState.SPEAKING

        if state == VADState.SPEAKING:
            self._ever_spoken = True
            self._silence_since = None
        elif state == VADState.QUIET and self._ever_spoken and self._silence_since is None:
            self._silence_since = self._clock()

        self._recompute()

    def feed_transcript(self, text: str, *, is_final: bool) -> None:
        if is_final:
            self._pending_transcript = text

        self._recompute()

    def turn_ended(self) -> bool:
        return self._turn_ended

    @property
    def last_final_transcript(self) -> str:
        """
        The transcript that ended the most recently completed turn - a
        stable snapshot, not the currently-accumulating buffer. Backed by a
        field _recompute() only ever writes once, at the exact moment it
        sets turn_ended True, and never clears afterward - so a caller
        (TurnDetectionProcessor) can read it whenever it gets around to
        building its turn_ended message, regardless of whether a *new*
        transcript for the *next* turn has already started arriving by
        then (found via a real, hanging-test-uncovered race: an earlier
        design backed this by the same field feed_transcript writes to,
        which reset_for_next_turn then had to clear to avoid reusing stale
        text for the next turn - but that clearing could run after a
        legitimately new transcript had already arrived, silently
        discarding it instead).
        """

        return self._ended_turn_text

    @property
    def is_speaking(self) -> bool:
        """
        Whether the most recently analyzed audio chunk was confirmed
        speech - current in real time, unaffected by turn_ended()'s latch
        or reset_for_next_turn(). Item 20e's barge-in signal is driven off
        this, not off the turn-ending state.
        """

        return self._is_speaking

    def reset_for_next_turn(self) -> None:
        """
        Rearm the detector to find a second, independent turn-ended cycle
        after this one. Only clears turn_ended - ever_spoken, silence_since,
        and pending_transcript are all deliberately left alone here.
        _recompute() already clears all three itself, synchronously, at the
        exact moment it sets turn_ended True (see there); feed_audio and
        feed_transcript both keep updating them unconditionally the whole
        time this stays latched afterward. Whatever they currently hold by
        the time this method runs already correctly reflects anything that
        happened during the latch - including a barge-in that spoke once
        and went quiet again, or even a new final transcript that arrived
        before this reset got around to running, since this reset is
        delivered asynchronously (a caller_speech_started message reacted
        to by a downstream processor), not synchronously alongside
        whatever caused it. Clearing any of the three here would throw
        that away.

        Calls _recompute() itself afterward - a real bug, found via a
        hanging end-to-end test, was this method not doing so. _recompute()
        otherwise only ever runs as a side effect of a *new* feed_audio or
        feed_transcript call; if the caller's entire new turn (speak, go
        quiet, get transcribed) already happened during the latch - and
        with an async-delivered reset, it can - there may be no further
        audio or transcript still to arrive that would otherwise trigger
        the check, leaving an already-satisfied turn undetected forever.
        """

        self._turn_ended = False
        self._recompute()

    def _recompute(self) -> None:
        if self._turn_ended or self._silence_since is None:
            return

        if is_semantically_complete(self._pending_transcript):
            self._ended_turn_text = self._pending_transcript
            self._pending_transcript = ""
            self._turn_ended = True
            self._ever_spoken = False
            self._silence_since = None
            return

        if self._clock() - self._silence_since >= FALLBACK_TIMEOUT_SECONDS:
            # An empty pending_transcript here means VAD found "speech" that
            # STT never actually transcribed anything for - a false
            # trigger (background noise, a mic pop, a breath, the
            # assistant's own TTS bleeding into the mic), not a caller who
            # trailed off mid-sentence. Ending a turn on nothing would send
            # an empty message to the LLM and produce a reply to silence -
            # the exact bug this guards against. A genuinely incomplete but
            # non-empty transcript (e.g. "and") still ends the turn as
            # before; only a transcript with no real content is withheld.
            if self._pending_transcript.strip():
                self._ended_turn_text = self._pending_transcript
                self._pending_transcript = ""
                self._turn_ended = True

            self._ever_spoken = False
            self._silence_since = None
