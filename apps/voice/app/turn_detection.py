"""
Pure turn-detection decision logic (item 20c) - VAD-detected sustained
silence plus a placeholder semantic-completeness check on the latest final
transcript, with a hard fallback timeout so an incomplete-sounding sentence
never leaves a caller in dead air forever. Deliberately no Pipecat or
WebSocket dependency here, mirroring items 17-19's chunker.py/
context_builder.py precedent - app/media_session.py is the thin adapter
that wires this into the live pipeline.
"""

import logging
import os
import time
from collections.abc import Callable

from app.adaptive_vad import AdaptiveNoiseFloor, AdaptiveVolumeVADAnalyzer
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADAnalyzer, VADParams, VADState

# VADParams.stop_secs at the two ends of AssistantVersion.turn_sensitivity's
# existing 0.0-1.0 range (item 11b) - 0.0 (most patient) waits nearly a
# second and a half of silence before VAD itself reports quiet again, 1.0
# (most eager) reports it after well under half a second. Starting values,
# not tuned against real call data - see this feature's spec for why that
# tuning is explicitly out of scope here.
logger = logging.getLogger(__name__)

_MIN_STOP_SECS = 0.3
_MAX_STOP_SECS = 1.5

# How certain Silero must be that a frame is speech, and how loud that frame
# must be, before it counts as the caller talking.
#
# Both sit above pipecat's own defaults (0.7 / 0.6), which are tuned for
# "is anyone speaking anywhere" rather than "is the person on this call
# speaking". A voice across the room reaches the microphone quieter and less
# cleanly than the caller's does, so the volume floor is the lever that
# separates them - and it has to be, because nothing downstream can: a turn
# only ever fires after the VAD has reported speech (see TurnDetector -
# _silence_since is set only once _ever_spoken is True), so whatever clears
# these thresholds is what the assistant will answer.
#
# Raising them trades one failure for another, which is why both are
# tunable: too low and the assistant answers the room, too high and it
# ignores a softly-spoken caller.
#
# **min_volume is not a fraction of full scale**, and reading it as one is
# how the default came to be set too high. Pipecat measures BS.1770
# integrated loudness normalized from -110..-10 LUFS, so the peak level each
# value demands has to be measured rather than guessed. Through pipecat's own
# calculate_audio_volume, with speech-shaped noise at 16kHz:
#
#     0.8 -> peak 4235/32768 (-17.8 dBFS), close-talking loud
#     0.7 -> peak 1341       (-27.8 dBFS)
#     0.6 -> peak  425       (-37.7 dBFS), pipecat's own default
#
# This project has now had both failures, in both directions, and the lesson
# is that the number cannot be chosen from a general claim about microphones.
#
# First the assistant answered voices in the room, and the floor went up.
# Then a machine whose microphone was delivering -46 dBFS heard nothing at
# all: the transcriber still produced words from it, the VAD never reported
# speech, no turn ever ended, and the caller sat in silence for a whole call.
# The floor came down to 0.6 to compensate. The microphone was then fixed -
# and 0.6, which had been compensating for it, started admitting the room
# again.
#
# So it is set from measured audio. Over 619 two-second windows of real calls
# the distribution is strongly bimodal: a background floor with a median peak
# of 448 (-37 dBFS), speech at p90 of 11466 (-9 dBFS), and a wide gap between
# roughly 1500 and 11000 where a threshold belongs. 0.8 asks for 4235, inside
# that gap. The share of a call each value calls speech says the rest: 0.6
# counts 53% of all audio as the caller talking, 0.7 counts 26%, 0.8 counts
# 15%. One participant in a conversation is not talking half the time.
#
# The honest limit: this is one absolute threshold serving two jobs - hear
# this caller, ignore that room - and it can only do both while the two are
# far apart in level. Making it relative to each call's own measured noise
# floor is the real answer and is not built.
_VAD_CONFIDENCE = float(os.environ.get("VAD_CONFIDENCE", "0.8"))
_VAD_MIN_VOLUME = float(os.environ.get("VAD_MIN_VOLUME", "0.8"))

# Whether the volume floor follows this call's own background instead of
# sitting at a fixed absolute level (app/adaptive_vad.py). On, because a
# fixed number has now failed in both directions here - too high and a
# quiet caller went unheard for a whole call, too low and the room got
# answered - and neither failure is a tuning mistake so much as a question
# an absolute threshold cannot answer. Set false to fall back to
# VAD_MIN_VOLUME alone.
# Off. It has been on once and it deafened a live call: the learned floor
# climbed into the caller's own voice and suppressed them for eight minutes.
# The estimator has since been rebuilt so that cannot happen (see
# adaptive_vad.py), but the margin it needs is calibrated against a
# deployment's own numbers, and shipping it on before those exist is the
# mistake that caused the outage. Turn it on after shadow mode says it would
# decide correctly.
_VAD_ADAPTIVE_FLOOR = os.environ.get("VAD_ADAPTIVE_FLOOR", "false").lower() == "true"

# Measure and log what the adaptive floor would decide, without letting it
# decide anything. The way to collect a real distribution from a real call at
# no risk to that call.
_VAD_ADAPTIVE_FLOOR_SHADOW = (
    os.environ.get("VAD_ADAPTIVE_FLOOR_SHADOW", "false").lower() == "true"
)

# How far above the measured room speech has to sit. See adaptive_vad.py.
_VAD_NOISE_MARGIN = float(os.environ.get("VAD_NOISE_MARGIN", "0.10"))

# What Silero's own volume gate is set to while the adaptive floor is doing
# the real work - low enough to defer to it, not zero, so a pathological
# noise floor still cannot make the analyzer accept pure silence.
_ADAPTIVE_DELEGATE_MIN_VOLUME = 0.3


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
    # With the adaptive floor on, Silero's own volume gate is deliberately
    # slack: the point is that this call's background decides the line, and
    # an absolute floor left at its usual value would keep overriding that -
    # rejecting a quiet caller in a quiet room before the adaptive gate is
    # ever consulted. Silero's *confidence* threshold does not move; it is
    # answering "is this speech at all", which does not depend on the room.
    # Shadow mode must not change what the caller experiences, so the
    # absolute floor stays exactly where it is; only the acting mode
    # slackens it so the measured background can decide.
    min_volume = (
        _ADAPTIVE_DELEGATE_MIN_VOLUME if _VAD_ADAPTIVE_FLOOR else _VAD_MIN_VOLUME
    )

    analyzer: VADAnalyzer = SileroVADAnalyzer(
        params=VADParams(
            stop_secs=sensitivity_to_stop_secs(sensitivity),
            confidence=_VAD_CONFIDENCE,
            min_volume=min_volume,
        )
    )

    if _VAD_ADAPTIVE_FLOOR or _VAD_ADAPTIVE_FLOOR_SHADOW:
        analyzer = AdaptiveVolumeVADAnalyzer(
            analyzer,
            noise_floor=AdaptiveNoiseFloor(margin=_VAD_NOISE_MARGIN),
            shadow=not _VAD_ADAPTIVE_FLOOR,
        )

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
        """
        A final transcript with no content is discarded rather than stored.
        ElevenLabs' realtime STT really does emit committed_transcript
        events with empty text (confirmed directly against the live API,
        not assumed) - typically trailing a genuine one. Letting one
        overwrite _pending_transcript would erase what the caller actually
        said, and _recompute() would then refuse to end the turn at all
        (an empty transcript is never semantically complete, and the
        fallback timeout deliberately withholds empty turns), leaving the
        caller in permanent silence with a reply that never comes.
        """

        if is_final and text.strip():
            if not self._ever_spoken:
                # The VAD has not confirmed the caller speaking since the
                # last turn ended, so whatever was transcribed was not them:
                # a voice across the room, a television, a passing
                # conversation. The transcriber hears the whole call and
                # commits anything it can make words out of, and it is much
                # more willing to do that than the VAD is to call something
                # speech.
                #
                # Dropping it does not change whether a turn fires - a turn
                # already cannot end without _ever_spoken - it changes what
                # the turn is *about*. Kept, this text sat in
                # _pending_transcript until the caller genuinely spoke, and
                # then went to the model as part of their question. Reported
                # as the assistant "randomly taking any voice and generating
                # any question".
                #
                # The word count, never the words (CLAUDE.md section 27).
                logger.info(
                    "ignoring a transcript the vad never heard: words=%d",
                    len(text.split()),
                )
            else:
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
    def heard_speech(self) -> bool:
        """
        Whether VAD has heard the caller speak since the last turn ended.

        A turn cannot end without this, so when one does not, this is half
        the answer to why - the other half being whether the transcript was
        a finished sentence.
        """

        return self._ever_spoken

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
