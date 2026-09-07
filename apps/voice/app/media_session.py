"""
Norma's own wrapper around Pipecat's transport/pipeline construction (item
20a's "behind Norma's own interfaces" requirement, CLAUDE.md section 5.5).
Application code should call build_voice_session_pipeline_worker() rather
than construct Pipecat primitives directly, so a future framework swap -
or 20d-20g adding real pipeline stages - only touches this module.
"""

import asyncio
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Sequence

from fastapi import WebSocket
from norma_shared.speech import (
    SpeechProviderError,
    SpeechToTextProvider,
    TextToSpeechProvider,
)
from pipecat.audio.vad.vad_analyzer import VADAnalyzer
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    InterruptionFrame,
    OutputAudioRawFrame,
    OutputTransportMessageUrgentFrame,
    StartFrame,
    TTSStoppedFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

from app import config
from app.conversation import ConversationState, assemble_system_prompt
from app.guardrails import (
    BLOCKED_TOPIC_REPLY,
    SAFE_FALLBACK,
    blocked_topic_in,
    find_unsupported_claim,
)
from app.llm import LLMProvider, LLMProviderError
from app.retrieval_client import fetch_retrieved_context
from app.sentence_chunker import SentenceChunker
from app.session_resilience import SessionResilienceTracker
from app.turn_detection import TurnDetector
from app.turn_metrics import TurnMetricsRecorder
from app.turn_metrics_client import record_turn_metric

# Fixed, generic apologies - never str(exception). CLAUDE.md's rule against
# exposing internal error details to a user applies to these JSON fallbacks
# exactly as much as to an HTTP error response.
_LLM_ERROR_MESSAGE = "Sorry, I'm having trouble responding right now."
_TTS_ERROR_MESSAGE = "Sorry, I'm having trouble speaking right now."

# Item 20g's one fixed apology for both failure reasons (STT or LLM) a
# session failover can have - see the spec's Out of scope for why this is
# deliberately not reason-specific or configurable.
_FAILOVER_MESSAGE = (
    "I'm sorry, I'm having trouble with the call right now. Please try again in a few minutes."
)

# Matches norma_shared/speech.py's canonical internal audio format (item
# 9a) on the control plane - the media plane should speak the same format
# its speech providers already assume.
AUDIO_SAMPLE_RATE_HZ = 16_000

# How often the incoming caller-audio level is reported (see
# SpeechToTextProcessor._observe_incoming_audio). Once every couple of
# seconds is enough to tell a silent stream from a live one without
# flooding a call's log.
_AUDIO_LEVEL_REPORT_SECONDS = 2.0

# 16-bit signed samples, matching norma_shared.speech's canonical format.
# Only used to convert a chunk's byte length into the seconds of speech it
# represents (see TTSProcessor._extend_playback).
_AUDIO_SAMPLE_WIDTH_BYTES = 2


# The only keys the browser is expected to report (see reportToServer in the
# test-call page). Item 24d: the log records these and nothing else, so a
# future client message carrying free text - or a tampered one carrying
# anything at all - cannot write itself into the call's log. The redacting
# formatter in norma_shared.logging_setup is the backstop; this is the lock.
_CLIENT_EVENT_FIELDS = ("source", "event", "reason", "queued", "stopped", "remaining")


def _log_client_event(data: str | bytes) -> None:
    """
    Log a browser telemetry message by its known fields only.

    Values are coerced to their repr rather than logged raw, so a string where
    a number was expected still cannot smuggle a sentence into the log.
    """

    try:
        payload = json.loads(data)
    except (ValueError, TypeError):
        logger.info("client event: unparseable (%d bytes)", len(data))
        return

    if not isinstance(payload, dict):
        logger.info("client event: unexpected shape %s", type(payload).__name__)
        return

    known = {key: payload[key] for key in _CLIENT_EVENT_FIELDS if key in payload}
    dropped = len(payload) - len(known)

    logger.info(
        "client event: %s%s",
        " ".join(f"{key}={value!r:.40}" for key, value in known.items()),
        f" (+{dropped} unrecognised field(s) dropped)" if dropped else "",
    )


class RawAudioFrameSerializer(FrameSerializer):
    """
    A binary WebSocket message is raw PCM audio bytes in, nothing else - no
    protocol, no framing. Enough to prove bidirectional audio streaming; a
    real telephony provider gets its own serializer (Pipecat already ships
    one each for Twilio/Telnyx/Plivo/Vonage/Genesys, for item 24+ to use
    directly rather than reinventing). An OutputTransportMessageUrgentFrame
    (transcripts, turn/LLM/TTS control signals) serializes out as a JSON
    text message instead of audio; an OutputAudioRawFrame (item 20e's
    synthesized speech) serializes out as raw bytes on the same connection.

    Verified empirically, not assumed: Pipecat's own TranscriptionFrame/
    InterimTranscriptionFrame never reach a serializer at all unless RTVI
    (a whole client-protocol layer this feature does not want) is enabled -
    the output transport's own frame dispatch only calls serialize() for
    OutputAudioRawFrame and OutputTransportMessageUrgentFrame. Message data
    is carried as a plain dict on that frame instead.
    """

    def __init__(self, *, sample_rate: int, num_channels: int = 1) -> None:
        super().__init__()
        self._sample_rate = sample_rate
        self._num_channels = num_channels

    async def serialize(self, frame: Frame) -> bytes | str | None:
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio

        if isinstance(frame, OutputTransportMessageUrgentFrame):
            return json.dumps(frame.message)

        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, bytes):
            return InputAudioRawFrame(
                audio=data,
                sample_rate=self._sample_rate,
                num_channels=self._num_channels,
            )

        # A text frame is the browser reporting what it did with what we
        # sent it - never audio, and never anything the pipeline acts on.
        # It exists purely so the client half of a call is observable from
        # the server's logs: whether a cancellation actually arrived there,
        # and what it did to the audio already queued. Without it, "the
        # server cancelled correctly but the caller kept hearing the reply"
        # is indistinguishable from "the message never arrived", which is
        # exactly the ambiguity this feature kept getting stuck on. Logged,
        # then dropped.
        _log_client_event(data)

        return None


class SpeechToTextProcessor(FrameProcessor):
    """
    Bridges Norma's SpeechToTextProvider contract (norma_shared.speech)
    into Pipecat's frame system - the "behind Norma's own interfaces"
    boundary for STT specifically. Feeds InputAudioRawFrame bytes into the
    provider's stream(), and pushes each yielded TranscriptEvent downstream
    as an OutputTransportMessageUrgentFrame carrying a plain
    {"type": "transcript", "text", "is_final"} dict - see
    RawAudioFrameSerializer's docstring for why not Pipecat's own
    TranscriptionFrame/InterimTranscriptionFrame.

    Hand-written rather than a pipecat.services.stt_service.STTService
    subclass: that base class's run_stt(audio: bytes) is a per-chunk
    contract, while SpeechToTextProvider.stream() owns the whole stream
    itself (see feature 20b's spec for the full reasoning) - forcing the
    whole-stream provider into the per-chunk base class would be an
    awkward, lossy fit.

    Forwards InputAudioRawFrame downstream after queuing it for STT - a
    deliberate revision of item 20b's original design, which consumed the
    frame here on the (then true) assumption that nothing downstream needed
    raw audio once STT had it. Item 20c's TurnDetectionProcessor needs the
    same audio for VAD, so it no longer holds.

    A failure from the provider's own stream() retries up to
    MAX_STT_STREAM_RETRIES times - a fresh stream() call against the same
    underlying audio queue, so any audio still queued (not yet handed to
    the broken stream) is not lost, though audio already in flight to the
    old stream when it broke is. Only once retries are exhausted does this
    trigger session failover (item 20g) - unlike a single LLM/TTS call,
    reconnecting mid-call while replaying whatever audio the old stream
    itself had already consumed but not yet transcribed is a materially
    bigger undertaking, out of scope here. Once STT itself is gone the
    caller can never be transcribed again for the rest of the call - the
    single most severe failure this feature addresses, so it is worth a
    few reconnect attempts before giving up on it.
    """

    def __init__(
        self,
        provider: SpeechToTextProvider,
        *,
        language: str,
        keywords: Sequence[str] = (),
    ) -> None:
        super().__init__()
        self._provider = provider
        self._language = language
        self._keywords = keywords
        self._audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._stream_task: asyncio.Task | None = None
        # Rolling window for _observe_incoming_audio's level reporting.
        self._level_frames = 0
        self._level_peak = 0
        self._level_reported_at = 0.0
        self._total_frames = 0
        # Whether *we* ended the audio input (EndFrame/CancelFrame). Tells a
        # provider stream that finished because the call is over apart from
        # one that closed under us mid-call - see _run_stream.
        self._input_ended = False

    async def _audio_iterator(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._audio_queue.get()

            if chunk is None:
                return

            yield chunk

    async def _run_stream(self) -> None:
        """
        Keeps the caller transcribed for the whole call.

        Two separate budgets, because the two failures mean different things.
        An exception is a sign something is actually wrong, and keeps its
        original small retry budget. A stream *closing cleanly* is not: the
        real provider does it routinely - measured both seconds into a
        session and again after four healthy minutes of one - and the call is
        still live either way, so it reconnects on a much larger budget.

        Treating a clean close as success is what produced the reported
        "assistant stops answering after a couple of replies": the stream
        handled two turns, ended, and every word spoken during the remaining
        minute of that call reached nothing at all - no transcript, no error,
        no failover, nothing in the log.
        """

        errors = 0
        reconnects = 0

        while True:
            try:
                logger.info(
                    "stt stream starting (errors=%d reconnects=%d)", errors, reconnects
                )
                events = 0

                # Held so it can be closed explicitly below. An abandoned
                # iterator stays suspended on its queue.get(), and that
                # pending waiter goes on consuming audio frames that the
                # replacement stream then never sees - so every reconnect
                # would leave behind another thief, starving each new stream
                # a little more until none of them transcribe anything at
                # all. Seen in a real session: reconnect counts climbing
                # past fifteen, every stream ending with zero events while
                # the caller's audio was arriving perfectly well.
                audio = self._audio_iterator()

                try:
                    async for event in self._provider.stream(
                        audio,
                        language=self._language,
                        keywords=self._keywords,
                    ):
                        events += 1

                        await self.push_frame(
                            OutputTransportMessageUrgentFrame(
                                message={
                                    "type": "transcript",
                                    "text": event.text,
                                    "is_final": event.is_final,
                                }
                            )
                        )
                finally:
                    await audio.aclose()

                logger.info("stt stream ended after %d events", events)

                # The only legitimate end: we stopped the audio ourselves
                # because the call is over.
                if self._input_ended:
                    return

                reconnects += 1

                # Announced once, then it keeps trying anyway: a call is
                # never abandoned because a provider is having a bad
                # minute. The caller decides when the call is over.
                if reconnects == config.MAX_STT_STREAM_RECONNECTS:
                    logger.error("stt stream has closed %d times - telling the caller", reconnects)
                    await self._announce_failover()

                logger.warning(
                    "stt stream closed mid-call after %d events - reconnecting", events
                )

                # Never a hot loop against a provider refusing connections,
                # and backing off rather than hammering one that is
                # struggling - capped so a call always recovers promptly
                # once it stops.
                await asyncio.sleep(
                    min(
                        config.STT_RECONNECT_DELAY_SECONDS * reconnects,
                        config.MAX_STT_RECONNECT_DELAY_SECONDS,
                    )
                )
            except SpeechProviderError as exc:
                errors += 1

                logger.warning("stt stream failed (error %d): %s", errors, exc)

                if errors == config.MAX_STT_STREAM_RETRIES + 1:
                    await self._announce_failover()

                # Falls through and retries: same reasoning as above.
                await asyncio.sleep(
                    min(
                        config.STT_RECONNECT_DELAY_SECONDS * errors,
                        config.MAX_STT_RECONNECT_DELAY_SECONDS,
                    )
                )
            except Exception:
                # Anything the provider contract did not anticipate. Without
                # this the task simply dies and the call goes deaf in
                # silence - no transcripts, no error, nothing in the log -
                # which is indistinguishable from a caller who never spoke.
                # CLAUDE.md: a provider failure must never produce silence.
                logger.exception("stt stream raised an unexpected error")
                await self._announce_failover()

                return

    async def _announce_failover(self) -> None:
        await self.push_frame(
            OutputTransportMessageUrgentFrame(
                message={
                    "type": "session_failover",
                    "reason": "stt_unavailable",
                    "message": _FAILOVER_MESSAGE,
                }
            )
        )

    def _observe_incoming_audio(self, chunk: bytes) -> None:
        """
        Periodically reports how loud the audio actually arriving from the
        caller is. Never the audio itself, and never anything transcribed -
        just a level, which is what distinguishes "the caller is not being
        heard at all" from "the caller is heard but not understood". Added
        after a browser-side microphone gate silently sent nothing but
        silence, which from the server looked identical to a caller who
        simply never spoke.
        """

        samples = memoryview(chunk).cast("h") if len(chunk) % 2 == 0 else None

        if samples is None or len(samples) == 0:
            return

        peak = 0

        for value in samples:
            magnitude = -value if value < 0 else value

            peak = max(peak, magnitude)

        self._level_frames += 1
        self._total_frames += 1
        self._level_peak = max(self._level_peak, peak)

        now = time.monotonic()

        if now - self._level_reported_at < _AUDIO_LEVEL_REPORT_SECONDS:
            return

        logger.info(
            "caller audio: frames=%d peak=%d (%.3f of full scale)",
            self._level_frames,
            self._level_peak,
            self._level_peak / 32768,
        )

        self._level_frames = 0
        self._level_peak = 0
        self._level_reported_at = now

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._stream_task = self.create_task(self._run_stream())
            await self.push_frame(frame, direction)
        elif isinstance(frame, InputAudioRawFrame):
            self._observe_incoming_audio(frame.audio)
            await self._audio_queue.put(frame.audio)
            await self.push_frame(frame, direction)
        elif isinstance(frame, (EndFrame, CancelFrame)):
            logger.info(
                "stt input ending on %s after %d frames", type(frame).__name__, self._total_frames
            )
            self._input_ended = True
            await self._audio_queue.put(None)
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)


logger = logging.getLogger(__name__)


def _is_transcript_message(message: object) -> bool:
    return isinstance(message, dict) and message.get("type") == "transcript"


def _normalized_words(text: str) -> list[str]:
    """
    Lowercased, punctuation-stripped words - so "Nine!" and "nine" compare
    equal. Comparing transcripts against spoken text has to survive an STT
    engine's own punctuation and casing choices, which never match the
    original wording exactly.
    """

    return [
        stripped
        for word in text.lower().split()
        if (stripped := "".join(character for character in word if character.isalnum()))
    ]


def _mostly_already_said(transcript: str, reference: str) -> bool:
    """
    Whether transcript is mostly made of words that already appear in
    reference - the test for "this is text we have seen before coming back
    to us" rather than the caller saying something new.

    Used for the two ways a mid-reply transcript can arrive without the
    caller having interrupted at all: the assistant's own playback picked up
    by an open mic, and a straggling final for the turn that just ended
    (STT and turn detection are independent pipelines, so a transcript can
    land after the turn it belongs to is already closed). Word overlap
    rather than equality because neither arrives verbatim.

    An empty transcript counts as already-said: there is nothing in it to
    treat as new speech. An empty reference does not - with nothing to have
    echoed, whatever arrived is genuinely new.
    """

    transcript_words = _normalized_words(transcript)

    if not transcript_words:
        return True

    reference_words = set(_normalized_words(reference))

    if not reference_words:
        return False

    matched = sum(1 for word in transcript_words if word in reference_words)

    return matched / len(transcript_words) >= config.ECHO_WORD_OVERLAP_RATIO


class TurnDetectionProcessor(FrameProcessor):
    """
    Bridges a TurnDetector (app/turn_detection.py) into Pipecat's frame
    system. Sits after SpeechToTextProcessor, observing the same
    InputAudioRawFrames (now forwarded downstream rather than consumed -
    see SpeechToTextProcessor's docstring) and the {"type": "transcript"}
    OutputTransportMessageUrgentFrames it emits. Once the detector reports
    the turn has ended, pushes a {"type": "turn_ended", "text": ...}
    message.

    Emits on the False->True edge of turn_ended(), not as a one-shot ever
    flag - item 20c's original design (a plain "already emitted" latch that
    never resets) was correct when only one turn ever needed proving, but
    would permanently block every turn after the first now that item 20d's
    LLMTurnProcessor calls reset_for_next_turn() between turns. Edge-
    triggering off the detector's own state re-arms automatically the
    moment it resets, with no direct reference between the two processors
    needed. Found while wiring in the multi-turn conversation loop.
    """

    def __init__(self, turn_detector: TurnDetector, turn_metrics: TurnMetricsRecorder) -> None:
        super().__init__()
        self._turn_detector = turn_detector
        self._turn_metrics = turn_metrics
        self._previously_ended = False
        self._previously_speaking = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            await self._turn_detector.feed_audio(frame.audio)
        elif isinstance(frame, OutputTransportMessageUrgentFrame) and _is_transcript_message(
            frame.message
        ):
            self._turn_detector.feed_transcript(
                frame.message["text"], is_final=frame.message["is_final"]
            )

        await self.push_frame(frame, direction)
        await self._maybe_emit_caller_speech_started()
        await self._maybe_emit_turn_ended()

    async def recheck(self) -> None:
        """
        Re-run the two emission checks below without any new frame having
        arrived to trigger them. Pipecat's pipeline is strictly
        unidirectional - TTSProcessor (downstream) calling
        turn_detector.reset_for_next_turn() can immediately find a *new*
        turn already complete (the caller's whole interruption - speak, go
        quiet, get transcribed - having happened during the latch, before
        the async-delivered reset got around to running), but nothing can
        make a further frame reach this processor afterward to trigger
        _maybe_emit_turn_ended() the normal way; every frame belonging to
        that new turn already flowed through here earlier, while still
        latched. TTSProcessor holds a direct reference to this instance
        (not just the shared TurnDetector) and calls this right after every
        reset_for_next_turn(), a deliberate, narrow exception to Norma's
        usual frame-only cross-processor communication.

        Forces _previously_ended back to False first. Without that, the
        edge-triggering in _maybe_emit_turn_ended() below would still miss
        this: _previously_ended was already True from the turn that just
        ended, and reset_for_next_turn()'s own brief False state lives and
        dies entirely inside its own synchronous call, never observed by
        this processor - so from here it looks like turn_ended() has been
        True the whole time, and the second turn's emission would be
        silently swallowed as "already reported." Found via a hanging
        end-to-end barge-in test: turn_ended was correctly True internally
        immediately after the reset, but its message was never pushed.
        """

        self._previously_ended = False
        await self._maybe_emit_caller_speech_started()
        await self._maybe_emit_turn_ended()

    async def _maybe_emit_caller_speech_started(self) -> None:
        """
        Edge-triggered off TurnDetector.is_speaking, which - unlike
        turn_ended() - stays live even while a reply is in flight (item
        20e's barge-in needs exactly that window). Fires on every genuine
        speech onset, turn-starting or interrupting alike; downstream
        processors that have nothing running simply no-op on it.
        """

        is_speaking = self._turn_detector.is_speaking

        if not is_speaking:
            self._previously_speaking = False
            return

        if self._previously_speaking:
            return

        self._previously_speaking = True

        await self.push_frame(
            OutputTransportMessageUrgentFrame(message={"type": "caller_speech_started"})
        )

    async def _maybe_emit_turn_ended(self) -> None:
        is_ended = self._turn_detector.turn_ended()

        if not is_ended:
            self._previously_ended = False
            return

        if self._previously_ended:
            return

        self._previously_ended = True
        self._turn_metrics.mark_stt_finalized(self._turn_metrics.current_generation())

        await self.push_frame(
            OutputTransportMessageUrgentFrame(
                message={
                    "type": "turn_ended",
                    "text": self._turn_detector.last_final_transcript,
                }
            )
        )


def _is_turn_ended_message(message: object) -> bool:
    return isinstance(message, dict) and message.get("type") == "turn_ended"


def _is_caller_speech_started_message(message: object) -> bool:
    return isinstance(message, dict) and message.get("type") == "caller_speech_started"


class LLMTurnProcessor(FrameProcessor):
    """
    Bridges an LLMProvider (app/llm.py) into Pipecat's frame system. Sits
    after TurnDetectionProcessor, observing its {"type": "turn_ended"}
    messages. On one arriving while no LLM call is in flight, fetches this
    turn's retrieved context, assembles the system prompt, and streams the
    reply as a tracked background task - never awaited inline, mirroring
    SpeechToTextProcessor's own precedent, since awaiting here would block
    all downstream frame processing (including the next turn's audio) for
    the entire response.

    Does NOT call turn_detector.reset_for_next_turn() itself - item 20e
    moved that ownership to TTSProcessor, since the reply is not actually
    over when the LLM finishes generating, only once the caller has heard
    all of it (or been cancelled by barge-in). Only one LLM turn ever runs
    at a time regardless: TurnDetector.turn_ended() stays latched True (so
    TurnDetectionProcessor cannot emit a second turn_ended) until whichever
    stage resets it. The is-a-call-already-running check below is a
    defensive invariant, not something reachable through the pipeline as
    currently wired; it costs nothing to keep.

    Also cancels its own in-flight task on caller_speech_started (item
    20e's barge-in signal) - otherwise a still-finishing LLM call would
    keep feeding text for an abandoned reply into a freshly-reset
    SentenceChunker downstream, and this also stops wasting LLM cost on a
    reply nobody will hear.

    Item 20g: each turn retries up to MAX_PROVIDER_RETRIES times, guarding
    only the *first* delta with a timeout (a stream already producing
    output is not hung - see this feature's spec for why a mid-stream
    stall is a documented, out-of-scope limitation instead). A turn that
    still fails after every retry pushes llm_error exactly as before, then
    reports itself to the shared SessionResilienceTracker; if that crosses
    the consecutive-failure threshold, also pushes session_failover -
    TTSProcessor is the sole consumer of that message.
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        turn_detector: TurnDetector,
        turn_metrics: TurnMetricsRecorder,
        session_resilience: SessionResilienceTracker,
        *,
        assistant_id: uuid.UUID,
        system_prompt: str,
        creativity: float,
        blocked_topics: Sequence[str] = (),
    ) -> None:
        super().__init__()
        self._llm_provider = llm_provider
        self._turn_detector = turn_detector
        self._turn_metrics = turn_metrics
        self._session_resilience = session_resilience
        self._assistant_id = assistant_id
        self._system_prompt = system_prompt
        self._creativity = creativity
        self._blocked_topics = tuple(blocked_topics)
        self._conversation = ConversationState()
        self._llm_task: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, OutputTransportMessageUrgentFrame) and _is_turn_ended_message(
            frame.message
        ):
            if self._llm_task is None or self._llm_task.done():
                # Captured once, here, at the moment this processor first
                # reacts to the turn - never re-read later inside
                # _run_llm_turn, which would defeat the generation guard
                # (see TurnMetricsRecorder's own docstring).
                generation = self._turn_metrics.current_generation()
                self._llm_task = self.create_task(
                    self._run_llm_turn(frame.message["text"], generation)
                )
        elif isinstance(
            frame, OutputTransportMessageUrgentFrame
        ) and _is_caller_speech_started_message(frame.message):
            if self._llm_task is not None:
                self._llm_task.cancel()
        elif isinstance(frame, (EndFrame, CancelFrame)) and self._llm_task is not None:
            self._llm_task.cancel()

        await self.push_frame(frame, direction)

    async def _run_llm_turn(self, caller_text: str, generation: int) -> None:
        # Appended once, before any retry - a retried attempt must never
        # duplicate the caller's own message in conversation history.
        self._conversation.append_user_turn(caller_text)

        # Checked before the model is called at all (item 24c). Enforcing it
        # here rather than by asking the model to refuse is the point: the
        # model never sees a blocked request, so no prompt wording and no
        # amount of caller persistence can talk it into answering. CLAUDE.md
        # section 36 - model output never authorizes anything.
        blocked = blocked_topic_in(caller_text, self._blocked_topics)

        if blocked is not None:
            # The matched topic, never the caller's words (section 27).
            logger.info(
                "turn refused: assistant=%s blocked_topic=%r", self._assistant_id, blocked
            )
            self._conversation.append_assistant_turn(BLOCKED_TOPIC_REPLY)
            await self.push_frame(
                OutputTransportMessageUrgentFrame(
                    message={"type": "llm_delta", "text": BLOCKED_TOPIC_REPLY}
                )
            )
            await self.push_frame(
                OutputTransportMessageUrgentFrame(
                    message={"type": "llm_complete", "text": BLOCKED_TOPIC_REPLY}
                )
            )

            return

        stream = None
        first_delta: str | None = None

        # Retry loop covers only "get to the first token" - nothing has
        # been spoken yet at this point, so restarting from scratch on
        # failure is safe. A single stream() call itself may re-fetch
        # retrieval each attempt; cheap and already independently resilient
        # (fetch_retrieved_context fails open on its own).
        for attempt in range(config.MAX_PROVIDER_RETRIES + 1):
            try:
                retrieved_context = await fetch_retrieved_context(
                    self._assistant_id, caller_text
                )
                self._turn_metrics.mark_retrieval_done(generation)
                system = assemble_system_prompt(
                    base_prompt=self._system_prompt, retrieved_context=retrieved_context
                )
                stream = self._llm_provider.stream(
                    self._conversation.messages, system=system, temperature=self._creativity
                )

                try:
                    first_delta = await asyncio.wait_for(
                        stream.__anext__(), timeout=config.LLM_FIRST_TOKEN_TIMEOUT_SECONDS
                    )
                except StopAsyncIteration:
                    first_delta = None

                break
            except (LLMProviderError, TimeoutError):
                if attempt < config.MAX_PROVIDER_RETRIES:
                    continue

                await self._give_up_on_turn()

                return

        # From here on, a failure means something has already been (or is
        # about to be) spoken - no retry, matching this pipeline's original,
        # pre-20g behavior exactly: give up immediately with llm_error for
        # whatever was said so far.
        try:
            reply_parts: list[str] = []
            # Sentence-gated so each one can be checked before the caller
            # hears it (item 24b). Costs no audible latency: TTSProcessor
            # already buffers through its own SentenceChunker and speaks
            # nothing until a sentence is complete.
            chunker = SentenceChunker()
            blocked = False

            async def emit(sentence: str) -> bool:
                """Push one sentence, or the fallback if it cannot be spoken."""

                reason = self._unsupported_claim_in(sentence, retrieved_context)

                if reason is None:
                    reply_parts.append(sentence)
                    await self.push_frame(
                        OutputTransportMessageUrgentFrame(
                            message={"type": "llm_delta", "text": sentence}
                        )
                    )

                    return True

                # Never the sentence itself - CLAUDE.md section 27.
                logger.warning(
                    "reply blocked: assistant=%s reason=%s", self._assistant_id, reason
                )
                reply_parts.append(SAFE_FALLBACK)
                await self.push_frame(
                    OutputTransportMessageUrgentFrame(
                        message={"type": "llm_delta", "text": SAFE_FALLBACK}
                    )
                )

                return False

            if first_delta is not None:
                self._turn_metrics.mark_llm_first_token(generation)

                for sentence in chunker.feed(first_delta):
                    if not await emit(sentence):
                        blocked = True
                        break

                if not blocked:
                    async for delta in stream:
                        for sentence in chunker.feed(delta):
                            if not await emit(sentence):
                                blocked = True
                                break

                        if blocked:
                            break

            # The trailing fragment is a real sentence the caller should hear
            # (see TTSProcessor._handle_llm_finished), so it is checked too
            # rather than dropped. A reply with no sentence-ending
            # punctuation at all arrives here as its only sentence.
            if not blocked:
                trailing = chunker.flush()

                if trailing:
                    await emit(trailing)

            # Joined with a space, not concatenated: reply_parts now holds
            # whole sentences from the chunker, which trims them, so
            # concatenation would run "...at nine.We close..." together in
            # the transcript and in the conversation history.
            full_reply = " ".join(reply_parts)
            self._conversation.append_assistant_turn(full_reply)
            self._turn_metrics.mark_llm_complete(generation)
            await self.push_frame(
                OutputTransportMessageUrgentFrame(
                    message={"type": "llm_complete", "text": full_reply}
                )
            )
            self._session_resilience.record_turn_succeeded()
        except LLMProviderError:
            await self._give_up_on_turn()

    def _unsupported_claim_in(self, sentence: str, grounded_text: str) -> str | None:
        """
        The validator's verdict, or None to speak - including when the
        validator itself fails.

        Fails open deliberately. The failure this guardrail could introduce -
        an assistant that answers "I don't have that" to everything - is
        worse than the one it removes, and far harder to notice: nobody sees
        a correct answer that was silently withheld. CLAUDE.md's "silence is
        the worst possible failure" points the same way.
        """

        try:
            return find_unsupported_claim(sentence, grounded_text=grounded_text)
        except Exception:
            logger.exception("grounding check failed - speaking the sentence anyway")

            return None

    async def _give_up_on_turn(self) -> None:
        """
        Pushes the existing llm_error message, then reports the failure to
        the shared SessionResilienceTracker - if that crosses the
        consecutive-failure threshold, also pushes session_failover.
        """

        await self.push_frame(
            OutputTransportMessageUrgentFrame(
                message={"type": "llm_error", "text": _LLM_ERROR_MESSAGE}
            )
        )

        if self._session_resilience.record_turn_failed():
            await self.push_frame(
                OutputTransportMessageUrgentFrame(
                    message={
                        "type": "session_failover",
                        "reason": "llm_unavailable",
                        "message": _FAILOVER_MESSAGE,
                    }
                )
            )


def _is_llm_delta_message(message: object) -> bool:
    return isinstance(message, dict) and message.get("type") == "llm_delta"


def _is_llm_reply_finished_message(message: object) -> bool:
    return isinstance(message, dict) and message.get("type") in ("llm_complete", "llm_error")


def _is_session_failover_message(message: object) -> bool:
    return isinstance(message, dict) and message.get("type") == "session_failover"


class TTSProcessor(FrameProcessor):
    """
    Bridges a TextToSpeechProvider into Pipecat's frame system. Sits after
    LLMTurnProcessor, observing its turn_ended/llm_delta/llm_complete/
    llm_error messages. Feeds delta text through a SentenceChunker and
    synthesizes + plays each complete sentence as soon as it is ready via a
    single sequential player task (sentences never overlap) - not waiting
    for llm_complete (CLAUDE.md: "start speaking before the LLM finishes").
    Synthesized audio is pushed as OutputAudioRawFrame.

    On caller_speech_started, cancels the currently-playing sentence,
    discards any still-queued sentences from that reply, resets the
    chunker, and resets the turn detector immediately - the caller's
    interruption ends the reply right there, regardless of what was still
    pending - but only if a reply is actually in progress. That is tracked
    via this processor's own local _reply_in_progress flag (set True on
    turn_ended - the moment a reply logically begins, even before the LLM
    has produced a single token - and False once reset, whether via normal
    completion or barge-in), deliberately not a check against
    turn_detector.turn_ended() itself. Verified empirically that Pipecat
    gives every FrameProcessor its own per-processor frame queue, so
    TurnDetectionProcessor can race ahead and mutate that *shared* object's
    state (e.g. already completing turn 1 while still processing frames
    behind schedule) before a caller_speech_started message it already
    pushed earlier has even been delivered to this processor's queue -
    peeking at the shared detector's live state would then answer a
    question about a *different, later* moment than the message this
    processor is actually reacting to. turn_ended and caller_speech_started
    are both pushed by the same upstream TurnDetectionProcessor and travel
    the same downstream chain, so their relative arrival order here is
    reliable even though the shared detector's live state is not. On a
    SpeechProviderError from the TTS provider, pushes tts_error for that
    one sentence and moves on to the next queued one rather than
    abandoning the whole reply.

    Owns turn_detector.reset_for_next_turn() for normal (non-barge-in)
    completion too - moved here from LLMTurnProcessor (item 20d) because
    the reply is not actually over when the LLM finishes generating, only
    once the caller has heard all of it. Checked in the two places either
    of "LLM signaled done" or "a sentence just finished playing" can newly
    become true; see this feature's spec for why both checks are needed.
    Every reset (this path or barge-in) pushes {"type": "reply_finished"} -
    the only observable signal that the reset, which now happens in this
    processor's own background task rather than synchronously alongside a
    message a caller already reads, has actually occurred.

    Also holds a direct reference to the upstream TurnDetectionProcessor
    (not just the shared TurnDetector) and calls its recheck() right after
    every reset_for_next_turn() - see TurnDetectionProcessor.recheck()'s
    own docstring for why a downstream processor resetting the detector
    cannot otherwise make the processor that owns turn_ended's *message*
    emission notice.

    Pushes a TTSStoppedFrame right after every sentence's own playback ends
    (whether it finished normally or was cancelled by barge-in) - not this
    feature's own concept, but a real Pipecat contract: the output
    transport's handle_audio_frame only auto-flushes complete
    audio_chunk_size chunks, leaving any smaller trailing remainder
    buffered indefinitely until a TTSStoppedFrame forces the flush.
    Without it, the last fraction-of-a-chunk of every sentence's audio -
    typically well under a second, but real, spoken content - would be
    silently dropped rather than ever reaching the caller. Found via a
    hanging end-to-end test whose received audio total came up short by
    exactly one partial chunk. Pushing it unconditionally, even on
    cancellation, is a deliberate, documented tradeoff: it also flushes
    (rather than discards) an abandoned sentence's own already-buffered
    tail, a few tens of milliseconds of stale audio bleeding past a
    barge-in - preferable to that same audio silently bleeding into the
    next reply instead, and to fully avoid it would mean wiring Pipecat's
    own InterruptionFrame/bot-speaking machinery, well beyond this
    feature's scope.
    """

    def __init__(
        self,
        tts_provider: TextToSpeechProvider,
        turn_detector: TurnDetector,
        turn_detection_processor: TurnDetectionProcessor,
        turn_metrics: TurnMetricsRecorder,
        *,
        assistant_id: uuid.UUID,
        voice_id: str,
        speech_rate: float,
    ) -> None:
        super().__init__()
        self._tts_provider = tts_provider
        self._turn_detector = turn_detector
        self._turn_detection_processor = turn_detection_processor
        self._turn_metrics = turn_metrics
        self._assistant_id = assistant_id
        self._voice_id = voice_id
        self._speech_rate = speech_rate
        self._chunker = SentenceChunker()
        self._sentence_queue: asyncio.Queue[str] = asyncio.Queue()
        self._player_task: asyncio.Task | None = None
        self._current_playback: asyncio.Task | None = None
        self._llm_finished = False
        self._reply_in_progress = False
        # Captured once, at the moment this processor first reacts to a
        # turn - see LLMTurnProcessor's identical precedent.
        self._active_generation = 0
        # Which generation's audio has already been marked - guards against
        # a second or later sentence's own first byte overwriting the
        # turn's true "time to first audio."
        self._audio_marked_generation: int | None = None
        # The next sentence's TTS first-chunk fetch, started as soon as
        # this one is known rather than only once the current sentence
        # finishes playing - see _play_sentences's own docstring for why.
        self._prefetch_task: asyncio.Task | None = None
        self._prefetch_sentence: str | None = None
        # The sentence most recently handed to the TTS provider, passed as
        # the next one's previous_text so the provider can carry prosody
        # across what are otherwise independent per-sentence generations
        # (see TextToSpeechProvider.synthesize). Cleared whenever a reply
        # ends or is abandoned: the next reply's opening sentence starts a
        # new utterance, with nothing before it to continue from.
        self._previous_sentence = ""
        # What this reply has actually put on the wire so far, and the
        # caller text that started it - the two things a mid-reply
        # transcript gets compared against before it is believed to be a
        # genuine interruption. See _handle_transcript.
        self._spoken_text = ""
        self._current_turn_text = ""
        # monotonic() time by which everything already pushed downstream
        # will have finished playing at the caller's end.
        #
        # Audio is sent as fast as TTS produces it, not in real time: a
        # ten-second reply reaches the client in about a second and is
        # scheduled for playback there. So the server finishes a reply -
        # empty queue, nothing playing, reply_in_progress False - while the
        # caller still has most of it to hear. Every interruption path
        # guarded on reply_in_progress alone was therefore dead by the time
        # a caller could realistically talk over anything, which is why
        # cancelling server-side had no audible effect. Measured directly
        # against a live session: reply_finished arrived a full second
        # before an interruption that was itself well inside the spoken
        # reply.
        self._playback_until = 0.0
        # Whether any audio for this reply has been handed to the output
        # transport, which buffers and paces it independently of this
        # processor - see _handle_barge_in.
        self._audio_outstanding = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._player_task = self.create_task(self._play_sentences())
        elif isinstance(frame, OutputTransportMessageUrgentFrame) and _is_turn_ended_message(
            frame.message
        ):
            await self._handle_turn_ended(frame.message.get("text", ""))
        elif isinstance(frame, OutputTransportMessageUrgentFrame) and _is_transcript_message(
            frame.message
        ):
            await self._handle_transcript(
                frame.message.get("text", ""),
                is_final=bool(frame.message.get("is_final")),
            )
        elif isinstance(frame, OutputTransportMessageUrgentFrame) and _is_llm_delta_message(
            frame.message
        ):
            await self._handle_delta(frame.message["text"])
        elif isinstance(
            frame, OutputTransportMessageUrgentFrame
        ) and _is_llm_reply_finished_message(frame.message):
            await self._handle_llm_finished(is_error=frame.message.get("type") == "llm_error")
        elif isinstance(
            frame, OutputTransportMessageUrgentFrame
        ) and _is_caller_speech_started_message(frame.message):
            await self._handle_barge_in()
        elif isinstance(
            frame, OutputTransportMessageUrgentFrame
        ) and _is_session_failover_message(frame.message):
            await self._handle_session_failover(frame.message["message"])
        elif isinstance(frame, (EndFrame, CancelFrame)):
            if self._player_task is not None:
                self._player_task.cancel()
            self._flush_and_post_if_anything_was_marked()

        await self.push_frame(frame, direction)

    async def _handle_delta(self, delta: str) -> None:
        self._reply_in_progress = True
        self._llm_finished = False

        for sentence in self._chunker.feed(delta):
            await self._sentence_queue.put(sentence)

    async def _handle_llm_finished(self, *, is_error: bool) -> None:
        """
        On llm_complete, the trailing buffered fragment is the tail of a
        genuinely intended reply (a real response the LLM meant to send,
        possibly just not yet punctuation-terminated) and gets spoken. On
        llm_error, any buffered fragment is instead an abandoned,
        mid-thought scrap - speaking a random cut-off word out of context
        would be a worse caller experience than staying silent for this
        turn, so it is discarded, not spoken.
        """

        if is_error:
            self._chunker.reset()
        else:
            trailing = self._chunker.flush()

            if trailing:
                await self._sentence_queue.put(trailing)

        self._llm_finished = True

        await self._maybe_reset_after_reply()

    async def _handle_turn_ended(self, text: str = "") -> None:
        """
        A new turn's reply is about to stream in. text is what the caller
        said to end it, kept so _handle_transcript can recognise a
        straggling final for this same turn instead of mistaking it for an
        interruption.

        This deliberately does *not* also discard whatever the previous
        reply still has queued or playing, which was tried and reverted:
        turn_ended fires for every turn, including the ordinary case where
        the previous reply finished long ago, and using it to cancel
        playback put "cut the assistant off" on a path that does not
        actually mean the caller is speaking right now. Interruption is
        decided by _handle_barge_in and _handle_transcript, both of which
        do.
        """

        self._current_turn_text = text
        # The previous reply is over as far as anything new is concerned.
        self._spoken_text = ""
        self._reply_in_progress = True
        self._llm_finished = False
        self._active_generation = self._turn_metrics.current_generation()

    async def _handle_transcript(self, text: str, *, is_final: bool) -> None:
        """
        Treats the caller being transcribed saying something new, while a
        reply is playing, as an interruption - the second, VAD-independent
        way into _handle_barge_in.

        Why this exists: turn detection latches once a turn ends and only
        re-arms when the reply finishes (TurnDetector._recompute returns
        early while turn_ended is set), so words the caller speaks over a
        reply are captured and buffered but cannot become a new turn until
        that reply has played out in full. caller_speech_started is the only
        thing that breaks that latch early, and it fires on a VAD speech
        onset *edge* - which the assistant's own audio in an open mic can
        hold high straight through the caller starting to talk, so no edge
        arrives and the caller waits out an answer they already interrupted.
        Transcribed words do not depend on that edge.

        Partials count as well as finals: ElevenLabs commits a final on its
        own VAD pauses, and a caller talking over a reply that is itself
        feeding the mic may not produce a pause it recognises for some time.
        Waiting for the commit would leave the reply running exactly as long
        as the bug being fixed. Only two kinds of text are ignored:

        - the caller's own text for the turn already in progress, which
          straggles in after that turn closed (STT and turn detection are
          independent pipelines). Acting on it would cancel the reply to
          the very words that asked for it.
        - text mostly made of what this reply is already saying, which is
          the assistant's own playback returning through the mic.

        Both guards are why this cannot simply trust any mid-reply
        transcript: without them, on the exact speaker setup this is meant
        to fix, every reply would cut itself off within a sentence.
        """

        if not config.BARGE_IN_ON_TRANSCRIPT:
            return

        # Not reply_in_progress alone: that is already False for most of the
        # time the caller can actually hear the reply (see _playback_until).
        if not text.strip():
            return

        # Per-partial, so debug rather than info - but the single most useful
        # line there is when an interruption "does nothing", since it shows
        # whether the server believed anything was playing at the time.
        if self._reply_in_progress or self._audio_still_playing():
            # Logged only while the assistant is actually audible: that is
            # the rare case and the only interesting one. With the caller's
            # microphone gated during playback, a transcript reaching here at
            # all is either a real interruption or echo that got through, and
            # the next line says which.
            logger.info(
                "transcript during playback: assistant=%s is_final=%s words=%d "
                "in_progress=%s playing=%s playback_left=%.2fs",
                self._assistant_id,
                is_final,
                len(_normalized_words(text)),
                self._reply_in_progress,
                self._audio_still_playing(),
                max(0.0, self._playback_until - time.monotonic()),
            )
        else:
            return

        # Never the transcript text itself (CLAUDE.md section 27) - only
        # enough about the decision to tell, from a real call's logs,
        # whether an interruption was seen, and if it was ignored, which
        # guard ignored it.
        if _mostly_already_said(text, self._current_turn_text):
            logger.info(
                "barge-in candidate ignored as own turn: assistant=%s is_final=%s words=%d",
                self._assistant_id,
                is_final,
                len(_normalized_words(text)),
            )

            return

        if _mostly_already_said(text, self._spoken_text):
            logger.info(
                "barge-in candidate ignored as echo: assistant=%s is_final=%s words=%d",
                self._assistant_id,
                is_final,
                len(_normalized_words(text)),
            )

            return

        logger.info(
            "barge-in from transcript: assistant=%s is_final=%s words=%d",
            self._assistant_id,
            is_final,
            len(_normalized_words(text)),
        )

        await self._handle_barge_in()

    async def _handle_barge_in(self) -> None:
        """
        caller_speech_started fires on every speech onset, not just an
        interruption (see TurnDetectionProcessor) - an ordinary
        turn-starting utterance has nothing in flight here to cancel or
        announce as finished, so this is a genuine no-op unless a reply is
        actually in progress. Guarded on this processor's own local
        _reply_in_progress flag, not turn_detector.turn_ended() - see the
        class docstring for why peeking at that shared, concurrently-raced
        object would be unreliable here.
        """

        was_in_progress = self._reply_in_progress

        logger.info(
            "barge-in signal: assistant=%s in_progress=%s playing=%s playback_left=%.2fs",
            self._assistant_id,
            was_in_progress,
            self._audio_still_playing(),
            max(0.0, self._playback_until - time.monotonic()),
        )

        # Where the caller's audio actually is.
        #
        # Not this processor, and not the browser: measured on a live call,
        # the browser holds about 60ms while this processor already believes
        # the reply finished seconds ago. The backlog sits in Pipecat's
        # output transport, which paces frames out in real time long after
        # they were handed over - so cancelling tasks here, or flushing the
        # browser, stops nothing the caller is still listening to.
        # InterruptionFrame is what makes the transport drop it (its
        # handle_interruptions clears the audio buffers), and it has to be
        # sent whenever the caller speaks over a reply - including when the
        # bookkeeping below thinks there is nothing left in flight, which is
        # exactly the case that kept this bug alive.
        if self._audio_outstanding:
            self._audio_outstanding = False

            await self.push_frame(InterruptionFrame())
            await self._announce_playback_cancelled()

        if not was_in_progress and not self._audio_still_playing():
            return

        self._discard_reply_in_flight()

        if not was_in_progress:
            # The reply was already finished and its turn already reset -
            # only the audio the caller had yet to hear needed dropping.
            # Resetting again here would announce a second reply_finished
            # for a reply that ended cleanly on its own.
            return

        self._llm_finished = False
        self._reply_in_progress = False
        await self._reset_turn()

    def _extend_playback(self, chunk: bytes) -> None:
        """
        Books this chunk's own duration onto the end of what the caller
        still has left to hear. Audio queues at the client, so a chunk sent
        while earlier audio is still playing lands after it, not now.
        """

        seconds = len(chunk) / (AUDIO_SAMPLE_RATE_HZ * _AUDIO_SAMPLE_WIDTH_BYTES)
        self._playback_until = max(time.monotonic(), self._playback_until) + seconds

    def _audio_still_playing(self) -> bool:
        """
        Whether the caller is still listening to audio already sent. The
        only reliable "is the assistant speaking right now" this side of
        the connection has - reply_in_progress goes False as soon as the
        last chunk is *handed over*, seconds before it is heard.
        """

        return time.monotonic() < self._playback_until

    async def _announce_playback_cancelled(self) -> None:
        """
        Tells the client to drop the audio it has already been sent but not
        yet played.

        Cancelling here only stops *sending*. Audio is streamed ahead of
        playback and scheduled locally at the other end, so on its own a
        server-side barge-in leaves the caller still listening to seconds of
        already-delivered speech - the reply audibly carrying on after being
        interrupted, which is the whole bug barge-in exists to prevent. The
        client used to flush on caller_speech_started alone, which is
        exactly the signal that does not arrive when a VAD onset edge is
        missed, so a transcript-driven barge-in silently had no audible
        effect at all.

        Deliberately its own message rather than reusing reply_finished,
        which also fires when a reply ends normally - flushing on that would
        clip the last moment off every untroubled reply.
        """

        await self.push_frame(
            OutputTransportMessageUrgentFrame(message={"type": "playback_cancelled"})
        )

    def _discard_reply_in_flight(self) -> None:
        """
        Drops everything belonging to the reply currently being spoken: the
        sentences still waiting their turn, the one actually playing, the
        next one's prefetched TTS fetch, and any partial sentence still
        buffered in the chunker. Shared by every path that abandons a reply
        (barge-in, a new turn superseding it, session failover) - each of
        which then differs in what it does *afterwards*, which is why the
        turn/generation bookkeeping deliberately stays with the callers.
        """

        while not self._sentence_queue.empty():
            self._sentence_queue.get_nowait()

        if self._current_playback is not None:
            self._current_playback.cancel()

        self._cancel_prefetch()
        self._chunker.reset()
        self._previous_sentence = ""
        self._spoken_text = ""
        # Nothing of this reply will be heard past the flush that
        # accompanies every discard.
        self._playback_until = 0.0

    def _cancel_prefetch(self) -> None:
        """
        Cancels and clears any in-flight next-sentence prefetch (see
        _play_sentences) - called everywhere the current reply is being
        abandoned (barge-in, session failover), mirroring exactly how
        _current_playback itself is cancelled at each of those same call
        sites. Without this, an abandoned reply's prefetch would either
        keep running as a wasted TTS call, or - worse - get handed to
        _play_sentences's next loop iteration as if it belonged to a new
        turn.
        """

        if self._prefetch_task is not None:
            self._prefetch_task.cancel()
            self._prefetch_task = None
            self._prefetch_sentence = None

    async def _handle_session_failover(self, apology_text: str) -> None:
        """
        Item 20g: the session cannot continue (SpeechToTextProcessor's
        stream crashed, or LLMTurnProcessor's consecutive-failure threshold
        was crossed) - pushed by either processor, this is the sole
        consumer. Deliberately bypasses the sentence queue/chunker/
        generation machinery entirely rather than routing through the
        normal llm_delta/llm_complete channel: that channel carries real
        coupling to this feature line's own turn/generation tracking (item
        20e's _reply_in_progress, item 20f's generation-guarded marks) that
        a synthetic, non-caller-originated "turn" would either have to fake
        correctly or silently corrupt - see this feature's spec.

        Cancels whatever reply is in flight, then attempts one bounded,
        best-effort synthesis of the fixed apology (no retry - the session
        is ending regardless, and a hung TTS call must not prevent the
        pipeline from ever closing). If TTS is also unavailable, or itself
        times out, the apology is simply skipped - EndFrame still follows,
        since closing the session is the one thing that must always
        eventually happen.
        """

        self._discard_reply_in_flight()
        # Without this the apology would be heard *behind* whatever of the
        # abandoned reply the client had already buffered.
        await self._announce_playback_cancelled()

        try:
            gen = self._tts_provider.synthesize(
                apology_text, voice_id=self._voice_id, speed=self._speech_rate
            )

            try:
                first_chunk = await asyncio.wait_for(
                    gen.__anext__(), timeout=config.TTS_FIRST_BYTE_TIMEOUT_SECONDS
                )
            except StopAsyncIteration:
                first_chunk = None

            if first_chunk is not None:
                await self.push_frame(
                    OutputAudioRawFrame(
                        audio=first_chunk, sample_rate=AUDIO_SAMPLE_RATE_HZ, num_channels=1
                    )
                )

                async for chunk in gen:
                    await self.push_frame(
                        OutputAudioRawFrame(
                            audio=chunk, sample_rate=AUDIO_SAMPLE_RATE_HZ, num_channels=1
                        )
                    )

            await self.push_frame(TTSStoppedFrame())
        except (SpeechProviderError, TimeoutError):
            pass

        # Deliberately does NOT end the session. A test call belongs to the
        # person on it: it ends when they disconnect, never because a
        # provider had trouble. This used to push EndFrame here, which hung
        # up on the caller mid-session - reported twice as "the test call
        # ended automatically, that should not happen". The apology above
        # tells them something went wrong; staying connected lets the
        # recovery underway (see SpeechToTextProcessor._run_stream, which
        # keeps reconnecting) actually reach them.

    async def _maybe_reset_after_reply(self) -> None:
        if (
            self._llm_finished
            and self._sentence_queue.empty()
            and self._current_playback is None
            and self._prefetch_task is None
        ):
            self._reply_in_progress = False
            self._previous_sentence = ""
            # _spoken_text deliberately survives here: the caller is still
            # hearing this reply for some seconds yet (see _playback_until),
            # and it is what tells that audio coming back through an open
            # mic apart from the caller genuinely interrupting. It is
            # cleared when the next turn begins.
            await self._reset_turn()

    async def _reset_turn(self) -> None:
        """
        Resets the detector and announces it via a {"type": "reply_finished"}
        message - the only observable signal that this reset (which now
        happens in this processor's own background task, not synchronously
        alongside a message a caller already reads, like item 20d's
        LLMTurnProcessor-owned reset did) has actually happened. Without
        this, nothing - not even a test - can tell "the LLM's reply text is
        done" (llm_complete) apart from "the caller has actually heard all
        of it and a new turn can now be detected" (this).
        """

        self._turn_detector.reset_for_next_turn()
        self._finish_turn_and_post()
        await self._turn_detection_processor.recheck()

        await self.push_frame(
            OutputTransportMessageUrgentFrame(message={"type": "reply_finished"})
        )

    def _finish_turn_and_post(self) -> None:
        """
        Snapshots and clears the accumulated record, then fires a
        fire-and-forget POST of whatever legs it reached - never awaited
        inline (CLAUDE.md: "No blocking I/O in the audio path"), and never
        skipped just because some legs are missing ("every turn writes a
        row" tolerates a partial one). Uses this processor's own
        create_task, not raw asyncio.create_task, so Pipecat's task manager
        holds the strong reference - a plain asyncio.create_task result
        with nothing else referencing it is eligible for garbage collection
        mid-flight. Called before recheck() specifically - see the class
        docstring and TurnMetricsRecorder's own for why a barge-in's second
        turn must never be handed a stale generation.
        """

        completed = self._turn_metrics.finish_turn()
        self.create_task(record_turn_metric(self._assistant_id, completed))

    def _flush_and_post_if_anything_was_marked(self) -> None:
        """
        EndFrame/CancelFrame means the connection is closing - possibly
        mid-reply, the single most realistic way a turn ends abnormally (the
        caller just hangs up). _reset_turn() is never reached in that case,
        so without this, that turn's entire timing record would be silently
        lost rather than written with whatever legs it reached. Skips the
        post entirely if nothing was ever marked - a session that never
        started a turn has nothing worth recording.
        """

        completed = self._turn_metrics.finish_turn()

        if completed.has_any_leg():
            self.create_task(record_turn_metric(self._assistant_id, completed))

    async def _play_sentences(self) -> None:
        """
        Plays each queued sentence in order, but the network round-trip to
        fetch the *next* sentence's first chunk is kicked off as soon as
        that sentence is known - typically as soon as the current one
        starts playing - rather than only once the current one finishes.
        Without this, every sentence boundary in a multi-sentence reply
        carried a real, audible dead-air gap while the next sentence's own
        TTS first-byte round-trip ran with nothing else happening -
        exactly what made a reply sound like it was "being read one
        disconnected line at a time" rather than spoken continuously. Only
        the fetch is prefetched, never the audio itself: pushing a second
        sentence's frames before the first has finished playing would
        interleave two sentences' audio on the same output stream.
        """

        while True:
            if self._prefetch_task is not None:
                sentence = self._prefetch_sentence
                fetch_task = self._prefetch_task
                self._prefetch_task = None
                self._prefetch_sentence = None
            else:
                sentence = await self._sentence_queue.get()
                fetch_task = None

            self._current_playback = self.create_task(
                self._speak(sentence, fetch_task, previous_text=self._previous_sentence)
            )

            # Anything synthesized from here on continues this sentence.
            # Set after the _speak call above so that one still sees what
            # preceded *it*, not itself.
            self._previous_sentence = sentence
            # Recorded as soon as the sentence starts playing, not once it
            # finishes: its audio is reaching the caller (and any open mic)
            # from this moment, so _handle_transcript has to be able to
            # recognise it coming back straight away.
            self._spoken_text = f"{self._spoken_text} {sentence}".strip()

            if not self._sentence_queue.empty():
                self._prefetch_sentence = self._sentence_queue.get_nowait()
                self._prefetch_task = self.create_task(
                    self._fetch_first_chunk(self._prefetch_sentence, previous_text=sentence)
                )

            try:
                await self._current_playback
            except asyncio.CancelledError:
                # A real bug found while testing item 20f's mid-reply
                # disconnect flush: barge-in cancels only _current_playback
                # (this player task itself keeps running, correctly falling
                # through to _maybe_reset_after_reply() below), but
                # EndFrame/CancelFrame cancels this *player task itself* -
                # and since cancelling a task that is currently awaiting a
                # child also cancels that child, both land here as the same
                # CancelledError. Task.cancelling() distinguishes them: it
                # is only nonzero when this task's own cancellation (not
                # just the child's) was requested. Re-raising in that case
                # actually stops this loop, instead of silently swallowing
                # it and spuriously calling _maybe_reset_after_reply() one
                # extra time on a connection that is already closing.
                if asyncio.current_task().cancelling():
                    self._cancel_prefetch()
                    raise
            finally:
                self._current_playback = None

            # Pipecat's output transport only auto-flushes *complete*
            # audio_chunk_size chunks (its own handle_audio_frame) - any
            # trailing remainder smaller than one chunk sits buffered
            # forever unless a TTSStoppedFrame arrives to force the flush.
            # Without this, the last fraction of a second of every sentence
            # (and, on barge-in, the just-cancelled sentence's own tail)
            # would be silently dropped rather than reaching the caller -
            # confirmed empirically via a hanging end-to-end test whose
            # audio total came up short by exactly one partial chunk.
            await self.push_frame(TTSStoppedFrame())

            await self._maybe_reset_after_reply()

    async def _fetch_first_chunk(
        self, sentence: str, *, previous_text: str = ""
    ) -> tuple[AsyncIterator[bytes], bytes] | None:
        """
        Phase 1 of speaking a sentence: opens the TTS stream and waits for
        its first chunk - the expensive network round-trip _play_sentences
        prefetches ahead of when a sentence is actually due to play. Retry
        covers only this phase, mirroring LLMTurnProcessor's own two-phase
        split and the real bug found building it (see this feature's
        spec): retrying a failure that happens *after* audio has already
        played would replay this sentence's already-spoken start. Returns
        None - after pushing tts_error, for a real failure; silently, for
        an empty synthesis (e.g. zero-length text) - either way there is
        nothing for the caller to play.
        """

        for attempt in range(config.MAX_PROVIDER_RETRIES + 1):
            try:
                gen = self._tts_provider.synthesize(
                    sentence,
                    voice_id=self._voice_id,
                    speed=self._speech_rate,
                    previous_text=previous_text,
                )

                try:
                    first_chunk = await asyncio.wait_for(
                        gen.__anext__(), timeout=config.TTS_FIRST_BYTE_TIMEOUT_SECONDS
                    )
                except StopAsyncIteration:
                    return None  # empty synthesis - nothing to play, not an error

                return gen, first_chunk
            except (SpeechProviderError, TimeoutError):
                if attempt < config.MAX_PROVIDER_RETRIES:
                    continue

                await self.push_frame(
                    OutputTransportMessageUrgentFrame(
                        message={"type": "tts_error", "text": _TTS_ERROR_MESSAGE}
                    )
                )

                return None

        return None  # unreachable - the loop above always returns or continues

    async def _speak(
        self,
        sentence: str,
        fetch_task: "asyncio.Task[tuple[AsyncIterator[bytes], bytes] | None] | None" = None,
        *,
        previous_text: str = "",
    ) -> None:
        """
        Phase 2: streams a sentence's audio out once its first chunk is in
        hand. fetch_task, when given, is _play_sentences's own prefetch for
        this exact sentence, already running (or already done) before this
        is even called - awaiting it here costs nothing extra when it
        finished early, and still correctly waits it out when it has not.
        When not given (the first sentence of a turn - nothing was playing
        yet for it to have overlapped with), fetches fresh here instead.
        """

        if fetch_task is not None:
            result = await fetch_task
        else:
            result = await self._fetch_first_chunk(sentence, previous_text=previous_text)

        if result is None:
            return

        gen, first_chunk = result

        # From here on, a failure means audio has already played (or is
        # about to) - no retry, matches this pipeline's original,
        # pre-20g behavior exactly.
        try:
            # Only the *first* sentence's first chunk of the turn - this
            # answers "time to first audio," not "time to every sentence."
            # Guarded on _active_generation (captured once, at turn_ended)
            # so a stale, already-superseded turn's late audio can never
            # mark the wrong row.
            if self._audio_marked_generation != self._active_generation:
                self._audio_marked_generation = self._active_generation
                self._turn_metrics.mark_tts_first_byte(self._active_generation)
                self._turn_metrics.mark_audio_out(self._active_generation)

            self._audio_outstanding = True
            self._extend_playback(first_chunk)

            await self.push_frame(
                OutputAudioRawFrame(
                    audio=first_chunk, sample_rate=AUDIO_SAMPLE_RATE_HZ, num_channels=1
                )
            )

            async for chunk in gen:
                self._extend_playback(chunk)

                await self.push_frame(
                    OutputAudioRawFrame(
                        audio=chunk, sample_rate=AUDIO_SAMPLE_RATE_HZ, num_channels=1
                    )
                )
        except SpeechProviderError:
            await self.push_frame(
                OutputTransportMessageUrgentFrame(
                    message={"type": "tts_error", "text": _TTS_ERROR_MESSAGE}
                )
            )


def build_voice_session_pipeline_worker(
    websocket: WebSocket,
    provider: SpeechToTextProvider,
    llm_provider: LLMProvider,
    tts_provider: TextToSpeechProvider,
    *,
    assistant_id: uuid.UUID,
    call_id: uuid.UUID,
    language: str = "en",
    keywords: Sequence[str] = (),
    sensitivity: float = 0.5,
    system_prompt: str = "",
    creativity: float = 0.3,
    blocked_topics: Sequence[str] = (),
    voice_id: str = "default",
    speech_rate: float = 1.0,
    vad_analyzer: VADAnalyzer | None = None,
) -> PipelineWorker:
    """
    Wire one WebSocket connection into a Pipecat pipeline that transcribes
    incoming audio, detects when the caller's turn has ended, streams an
    LLM reply, and speaks it - synthesized audio and JSON control messages
    both go back to the caller over the same connection. This is the only
    Pipecat-specific construction in the media plane - later items add real
    stages to the Pipeline list here, not by reaching into Pipecat from
    elsewhere in the app.

    vad_analyzer is exposed for tests to inject a scripted fake - the real
    SileroVADAnalyzer loads an ML model and should never run in the test
    suite (see app/turn_detection.py).

    call_id (item 20f) is generated once per session by the caller (see
    app/main.py) - a session-scoped placeholder identity for TurnMetric
    rows, since Call (build-plan item 27) doesn't exist yet.
    """

    transport = FastAPIWebsocketTransport(
        websocket,
        FastAPIWebsocketParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_sample_rate=AUDIO_SAMPLE_RATE_HZ,
            audio_out_sample_rate=AUDIO_SAMPLE_RATE_HZ,
            serializer=RawAudioFrameSerializer(sample_rate=AUDIO_SAMPLE_RATE_HZ),
        ),
    )

    turn_detector = TurnDetector(
        sensitivity=sensitivity,
        sample_rate=AUDIO_SAMPLE_RATE_HZ,
        vad_analyzer=vad_analyzer,
    )

    turn_metrics = TurnMetricsRecorder(call_id=call_id)
    turn_detection_processor = TurnDetectionProcessor(turn_detector, turn_metrics)
    session_resilience = SessionResilienceTracker(
        max_consecutive_failures=config.MAX_CONSECUTIVE_LLM_FAILURES
    )

    pipeline = Pipeline(
        [
            transport.input(),
            SpeechToTextProcessor(provider, language=language, keywords=keywords),
            turn_detection_processor,
            LLMTurnProcessor(
                llm_provider,
                turn_detector,
                turn_metrics,
                session_resilience,
                assistant_id=assistant_id,
                system_prompt=system_prompt,
                creativity=creativity,
                blocked_topics=blocked_topics,
            ),
            TTSProcessor(
                tts_provider,
                turn_detector,
                turn_detection_processor,
                turn_metrics,
                assistant_id=assistant_id,
                voice_id=voice_id,
                speech_rate=speech_rate,
            ),
            transport.output(),
        ]
    )

    # enable_rtvi defaults to True, which intercepts message-carrying
    # frames into Pipecat's own RTVI client protocol before they ever
    # reach RawAudioFrameSerializer above - discovered empirically while
    # building this feature. RTVI is a whole client-protocol layer for
    # RTVI-aware SDKs, well beyond what this minimal proof needs; disabled
    # so transcript frames reach the caller in Norma's own simple JSON
    # shape instead.
    # idle_timeout_secs=None disables Pipecat's idle watchdog, which would
    # otherwise cancel the pipeline - ending the call - after five minutes.
    #
    # It decides "idle" by watching for BotSpeakingFrame/UserSpeakingFrame,
    # and this pipeline emits neither: it drives speech through Norma's own
    # processors and OutputAudioRawFrame rather than Pipecat's TTS and VAD
    # machinery, so the watchdog sees no activity however busy the call
    # actually is. Measured on a live call that was answering questions
    # continuously: "Idle timeout detected... cancelling pipeline" at
    # exactly the five-minute mark, which the caller experienced as the
    # test call hanging up on them mid-answer around the thirteenth
    # question. A call ends when the person on it disconnects.
    return PipelineWorker(pipeline, enable_rtvi=False, idle_timeout_secs=None)
