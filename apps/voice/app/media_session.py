"""
Norma's own wrapper around Pipecat's transport/pipeline construction (item
20a's "behind Norma's own interfaces" requirement, CLAUDE.md section 5.5).
Application code should call build_voice_session_pipeline_worker() rather
than construct Pipecat primitives directly, so a future framework swap -
or 20d-20g adding real pipeline stages - only touches this module.
"""

import asyncio
import contextlib
import json
import logging
import time
import uuid
from collections.abc import AsyncIterator, Sequence

from app import config
from app.conversation import ConversationState, assemble_system_prompt
from app.guardrails import (
    BLOCKED_TOPIC_REPLY,
    SAFE_FALLBACK,
    blocked_topic_in,
    find_unsupported_claim,
)
from app.llm import LLMProvider, LLMProviderError, LLMRateLimited
from app.llm_pricing import realtime_turn_cost_micro_usd
from app.retrieval_client import fetch_retrieved_context
from app.sentence_chunker import SentenceChunker
from app.session_resilience import SessionResilienceTracker
from app.spoken_text import to_spoken_text
from app.turn_detection import (
    TurnDetector,
    is_semantically_complete,
    sensitivity_to_stop_secs,
)
from app.turn_metrics import TurnMetricsRecorder
from app.turn_metrics_client import record_turn_metric
from fastapi import WebSocket
from norma_shared.correlation import CallContext
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

# Said when the transcriber has gone deaf but the call is still perfectly
# alive. Deliberately not _FAILOVER_MESSAGE: that one tells the caller to
# give up and try later, which is wrong for a stream that usually recovers
# within seconds. This one tells them what is happening and invites them to
# keep going, because silence leaves them believing the call has died - which
# is what actually happened on a real session where the stream went deaf
# while the microphone was working perfectly.
_HEARING_TROUBLE_MESSAGE = (
    "Sorry, I'm having trouble hearing you at the moment. Could you say that again?"
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

# The peak level, as a fraction of full scale, above which a two-second
# window of caller audio counts as speech rather than room noise. Measured
# from real sessions: an idle microphone sits at 0.003-0.04, and speech that
# transcribes correctly peaks anywhere from 0.13 upwards. 0.08 sits in the
# gap. It is only ever used to decide whether the caller is saying something
# the STT stream ought to be reacting to - never to gate audio, which is the
# mistake that made an earlier version of this file go deaf whenever the
# threshold was wrong.
_SPEECH_PEAK_FRACTION = 0.08

# The most caller audio that may sit waiting to be sent to the speech
# provider, in seconds. CLAUDE.md's "no unbounded queues - backpressure must
# be explicit", applied where the lack of it was doing real damage.
#
# The provider has a queue of its own, and when it overflows it says so once
# and then stops transcribing entirely for the life of that connection - the
# "assistant not answering anything" failure, confirmed from its own
# queue_overflow message. The way to overflow it is to hand it a burst, and
# the two ways a burst arises here are a browser that sends faster than
# realtime (measured: 149 and 165 frames in a two-second window, against 100
# at realtime) and a backlog built up while a stream was down, dumped at full
# speed into its replacement.
#
# Five seconds, which is comfortably more than the longest gap a healthy
# call has without a consumer - the reconnect backoff between streams, capped
# at two. It is a memory bound and a burst guard, not a latency control: with
# a stream actually running the queue sits at zero, and the number that says
# so is the "queued=" field on the audio-level line.
#
# It was two, and that was too tight to survive a separate bug in which
# nothing drained the queue at all: the cap then evicted a frame for every
# frame that arrived, 100 dropped per 100 received, and the caller went
# inaudible for the rest of the session. Dropping the oldest rather than
# refusing the newest is still right - audio that old cannot help a live
# call - but a cap this close to normal operation turned someone else's bug
# into total deafness rather than a bounded loss.
_MAX_QUEUED_AUDIO_SECONDS = 5.0

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
    one each for Twilio/Telnyx/Plivo/Vonage/Genesys, for item 26+ to use
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
        silence_threshold_secs: float | None = None,
    ) -> None:
        super().__init__()
        self._provider = provider
        self._language = language
        self._keywords = keywords
        self._silence_threshold_secs = silence_threshold_secs
        # Bounded by _queue_audio, not by maxsize: the cap is a duration of
        # audio rather than a count of frames, and a full queue must drop
        # the oldest frame rather than block the transport thread feeding
        # it. Unbounded here would violate CLAUDE.md 5.1 - it is enforced
        # one call up.
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
        # The two clocks the deafness watchdog compares: when the caller was
        # last audibly speaking, and when the stream last had anything to say
        # about it. Speech newer than the last transcript, for longer than
        # the watchdog's patience, means this stream has stopped listening.
        self._last_speech_at = 0.0
        self._last_event_at = 0.0
        # Consecutive watchdog restarts with nothing transcribed between
        # them, and when the caller was last told about it.
        self._deaf_restarts = 0
        self._told_about_deafness_at = 0.0
        self._watchdog_task: asyncio.Task | None = None
        # Bytes currently sitting in _audio_queue, so the backlog can be
        # capped by duration rather than by a frame count that would mean
        # different things for different frame sizes.
        self._queued_bytes = 0
        self._dropped_frames = 0
        self._dropped_since_report = 0
        self._dropped_reported_at = 0.0
        # Frames actually handed to the provider. The number that says
        # whether a full backlog means "the provider is behind" or "nothing
        # is draining this queue at all".
        self._frames_sent = 0
        # The current stream's consumer, and its running event count. The
        # watchdog cancels the task; the count survives it, so a cancelled
        # stream can still report how much it managed.
        self._consume_task: asyncio.Task[int] | None = None
        self._events_this_stream = 0

    async def _audio_iterator(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._audio_queue.get()

            if chunk is None:
                return

            self._queued_bytes -= len(chunk)
            self._frames_sent += 1

            yield chunk

    def _queue_audio(self, chunk: bytes, *, sample_rate: int) -> None:
        """
        Queue one frame for the provider, dropping the oldest audio if the
        backlog has grown past what is worth sending - see
        _MAX_QUEUED_AUDIO_SECONDS.
        """

        self._audio_queue.put_nowait(chunk)
        self._queued_bytes += len(chunk)

        # 16-bit samples, one channel.
        cap = int(_MAX_QUEUED_AUDIO_SECONDS * sample_rate * 2)
        dropped = 0

        while self._queued_bytes > cap:
            oldest = self._audio_queue.get_nowait()

            if oldest is None:
                # The end-of-stream sentinel. Never dropped - it is what
                # ends the current provider stream, and losing it would
                # leave that stream running with nothing to end it. Putting
                # it back sends it to the tail, which only means the stream
                # it ends consumes a little more audio first.
                self._audio_queue.put_nowait(None)
                break

            self._queued_bytes -= len(oldest)
            dropped += 1

        if not dropped:
            return

        self._dropped_frames += dropped
        self._dropped_since_report += dropped

        # Summarised on the same cadence as the audio-level line rather than
        # one line per frame. A burst drops one frame per frame that arrives,
        # so per-frame logging produced 84 identical warnings inside a single
        # second - noise that buries the very signal it exists to give.
        now = time.monotonic()

        if now - self._dropped_reported_at < _AUDIO_LEVEL_REPORT_SECONDS:
            return

        logger.warning(
            "caller audio arriving faster than the provider accepts it - "
            "dropped %d frames in the last %.0fs (%d this session), keeping "
            "the backlog under %.0fs",
            self._dropped_since_report,
            _AUDIO_LEVEL_REPORT_SECONDS,
            self._dropped_frames,
            _MAX_QUEUED_AUDIO_SECONDS,
        )

        self._dropped_since_report = 0
        self._dropped_reported_at = now

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
                self._last_event_at = time.monotonic()

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

                # Consumed in a task of its own so the deafness watchdog can
                # abandon a stream that has stopped responding.
                #
                # It used to end the audio iterator instead, by queueing the
                # sentinel. That looked equivalent and was not: it stops
                # anything draining the audio queue immediately, while the
                # stream itself goes on waiting for a server that has
                # already gone quiet to close the connection - which it
                # never did. The call was then left with no consumer at all,
                # every arriving frame evicting the one before it under the
                # backlog cap, and the caller inaudible for the rest of the
                # session: 100 frames arriving per two seconds and 100 being
                # dropped. Reported, again, as "assistant is not responding
                # anything".
                #
                # Cancelling the consumer instead propagates into the
                # provider's own generator, which closes its connection on
                # the way out, and the loop below reconnects with a fresh
                # stream that starts draining the queue again.
                self._consume_task = asyncio.create_task(self._consume_stream(audio))

                try:
                    events = await self._consume_task
                except asyncio.CancelledError:
                    # The watchdog abandoning this stream. A session
                    # teardown cancels _run_stream itself, not this task, so
                    # anything arriving here while the input has ended is
                    # the real thing and must not be swallowed.
                    if self._input_ended:
                        raise

                    events = self._events_this_stream
                finally:
                    # Awaiting a task does not cancel it, so a _run_stream
                    # cancelled during teardown would otherwise leave this
                    # one running - still holding the provider connection
                    # open for a call nobody is on. That is the zombie
                    # session this file has already been bitten by once:
                    # 126 of them looping at once, rate-limiting the speech
                    # provider for every real call.
                    consume_task = self._consume_task
                    self._consume_task = None

                    if consume_task is not None and not consume_task.done():
                        consume_task.cancel()

                        with contextlib.suppress(asyncio.CancelledError):
                            await consume_task

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

    async def _consume_stream(self, audio: AsyncIterator[bytes]) -> int:
        """
        Push every transcript the provider yields, and return how many there
        were. A task of its own purely so it can be cancelled - see
        _run_stream.
        """

        self._events_this_stream = 0

        async for event in self._provider.stream(
            audio,
            language=self._language,
            keywords=self._keywords,
            silence_threshold_secs=self._silence_threshold_secs,
        ):
            self._events_this_stream += 1
            self._last_event_at = time.monotonic()
            # Transcription is working again, so the run of deaf restarts is
            # over. Counted consecutively rather than cumulatively: a call
            # that hiccups once an hour is not one to keep apologising on.
            self._deaf_restarts = 0

            await self.push_frame(
                OutputTransportMessageUrgentFrame(
                    message={
                        "type": "transcript",
                        "text": event.text,
                        "is_final": event.is_final,
                    }
                )
            )

        return self._events_this_stream

    async def _watch_for_deafness(self) -> None:
        """
        Ends a stream that has stopped transcribing, so the reconnect loop
        can replace it.

        Every recovery path in _run_stream is driven by the provider telling
        us something - an exception, or a clean close. A stream that stays
        open and simply says nothing tells us neither, and there was no
        recovery from it at all: reported as "assistant not working", two
        sessions in a row where the caller's audio arrived for thirty-four
        seconds, several windows of it at clear speech level, and the log
        held one "stt stream starting" line and then nothing until they hung
        up.

        Ending the current audio iterator (a None on the queue) is all this
        does. That is the same shape as the provider closing its own stream,
        so _run_stream's existing "closed mid-call - reconnecting" path takes
        it from there, including the failover announcement once reconnects
        stop helping. _input_ended stays False, so this can never be mistaken
        for the call being over.

        Cannot fire on a caller who is simply quiet: it requires speech-level
        audio to have arrived *after* the last transcript. A caller thinking
        in silence produces neither, and is left alone.
        """

        while not self._input_ended:
            await asyncio.sleep(config.STT_DEAF_WATCHDOG_POLL_SECONDS)

            if self._input_ended:
                return

            # Nothing said since the stream last responded - not deafness,
            # just a pause.
            if self._last_speech_at <= self._last_event_at:
                continue

            silent_for = time.monotonic() - self._last_event_at

            if silent_for < config.STT_DEAF_WATCHDOG_SECONDS:
                continue

            logger.warning(
                "stt stream heard speech but returned nothing for %.0fs - "
                "restarting it",
                silent_for,
            )

            # Counts as a response for the purposes of this check, so the
            # replacement stream gets its own full window instead of being
            # torn down again on the next poll.
            self._last_event_at = time.monotonic()
            self._deaf_restarts += 1

            await self._maybe_say_it_cannot_hear()

            if self._consume_task is not None:
                self._consume_task.cancel()

    async def _maybe_say_it_cannot_hear(self) -> None:
        """
        Tell the caller the assistant cannot hear them, out loud.

        The existing failover announcement needs fifty reconnects to fire,
        which is right for a provider that has genuinely gone away and far
        too patient for this: on a real call the stream went deaf while the
        microphone was delivering healthy audio, the watchdog restarted it
        twice, and the caller heard nothing whatsoever for the whole
        session. CLAUDE.md is explicit that silence is the worst possible
        failure, and a caller who is told what is wrong can at least repeat
        themselves or hang up deliberately.

        Says nothing on the first restart - streams do close on their own
        and usually recover within a second, and narrating that would be
        noise. Repeats on a cooldown rather than once, because a caller who
        hears it and then nothing concludes the call is dead anyway.

        Never ends the session: a call belongs to the person on it (see
        _handle_session_failover).
        """

        if self._deaf_restarts < config.STT_HEARING_TROUBLE_RESTARTS:
            return

        now = time.monotonic()

        if now - self._told_about_deafness_at < config.STT_HEARING_TROUBLE_COOLDOWN_SECONDS:
            return

        self._told_about_deafness_at = now
        logger.warning(
            "telling the caller the assistant cannot hear them: restarts=%d",
            self._deaf_restarts,
        )

        await self.push_frame(
            OutputTransportMessageUrgentFrame(
                message={
                    "type": "hearing_trouble",
                    "message": _HEARING_TROUBLE_MESSAGE,
                }
            )
        )

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

    async def cleanup(self) -> None:
        """
        Stop transcribing when the session is torn down.

        The reconnect loop only ever exited on _input_ended, which is set by
        an EndFrame or CancelFrame. A browser simply closing its socket
        delivers neither - confirmed by the absence of this processor's own
        "stt input ending" line after a real disconnect - so the loop went on
        reconnecting to the provider for a session with nobody on it.

        Every test call left one behind. They accumulate: 126 of them were
        found still looping at once, between them 1,639 reconnects in an hour,
        which rate-limited the speech provider and Groq alike and left new,
        real calls with no transcription and no reply. Reported as "assistant
        isn't responding anything".

        Deliberately narrow. Reconnecting through a provider's own failures is
        the correct behaviour and is left alone (CLAUDE.md: silence is the
        worst possible failure, and a caller decides when a call is over).
        This only ends the loop once there is no longer a call to serve.
        """

        self._input_ended = True
        await self._audio_queue.put(None)

        if self._watchdog_task is not None:
            await self.cancel_task(self._watchdog_task)
            self._watchdog_task = None

        if self._stream_task is not None:
            await self.cancel_task(self._stream_task)
            self._stream_task = None

        await super().cleanup()

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

        if peak >= _SPEECH_PEAK_FRACTION * 32768:
            self._last_speech_at = time.monotonic()

        now = time.monotonic()

        if now - self._level_reported_at < _AUDIO_LEVEL_REPORT_SECONDS:
            return

        logger.info(
            "caller audio: frames=%d peak=%d (%.3f of full scale) "
            "queued=%d frames/%d bytes sent=%d",
            self._level_frames,
            self._level_peak,
            self._level_peak / 32768,
            self._audio_queue.qsize(),
            self._queued_bytes,
            self._frames_sent,
        )

        self._level_frames = 0
        self._level_peak = 0
        self._level_reported_at = now

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._stream_task = self.create_task(self._run_stream())
            self._watchdog_task = self.create_task(self._watch_for_deafness())
            await self.push_frame(frame, direction)
        elif isinstance(frame, InputAudioRawFrame):
            self._observe_incoming_audio(frame.audio)
            self._queue_audio(frame.audio, sample_rate=frame.sample_rate)
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
            text = frame.message["text"]
            is_final = bool(frame.message["is_final"])

            self._turn_detector.feed_transcript(text, is_final=is_final)

            if is_final:
                # Why a committed transcript did or did not end the turn.
                #
                # This decision has been invisible, and "the assistant is
                # not replying" has been misdiagnosed twice because of it -
                # the logs could show a stream delivering committed
                # transcripts and no LLM call, with nothing to say which of
                # the three conditions was unmet. Both inputs matter: the
                # provider commits empty transcripts on its own (see
                # TurnDetector.feed_transcript), and a turn also needs VAD
                # to have heard the caller.
                #
                # Word count and flags only, never the words (CLAUDE.md
                # section 27), matching the barge-in logs' precedent.
                logger.info(
                    "final transcript: words=%d complete=%s vad_heard_speech=%s "
                    "turn_ended=%s",
                    len(text.split()),
                    is_semantically_complete(text),
                    self._turn_detector.heard_speech,
                    self._turn_detector.turn_ended(),
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
        # Set once a rate limit has been hit on this turn, which changes what
        # a retry means: see the LLMRateLimited branch below.
        drop_context_to_fit = False

        for attempt in range(config.MAX_PROVIDER_RETRIES + 1):
            try:
                retrieved_context = await fetch_retrieved_context(
                    self._assistant_id, caller_text
                )
                self._turn_metrics.mark_retrieval_done(generation)

                if drop_context_to_fit:
                    retrieved_context = ""

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
            except LLMRateLimited as exc:
                # A per-minute token quota is the one failure retrying the
                # same request cannot fix - it is the request itself that is
                # over budget, and each attempt spends more of the window it
                # is waiting on. Measured on a real call: seven turns, then
                # three identical attempts inside eight seconds, all refused,
                # and the caller heard nothing.
                #
                # So the retry sheds the largest droppable part of the prompt
                # instead of repeating it. Retrieved context is roughly 40%
                # of a turn here (about 1,150 tokens of a 2,800-token
                # prompt), it is the only part that is optional, and dropping
                # it degrades the answer rather than removing it: the
                # guardrail then holds the model to what it can support, so
                # the caller hears "I don't have that detail" instead of
                # silence. Waiting out the window instead would be tens of
                # seconds of dead air, which CLAUDE.md calls the worst
                # possible failure.
                can_shed = (
                    attempt < config.MAX_PROVIDER_RETRIES and not drop_context_to_fit
                )
                # Says what actually happens next. The first version of this
                # line claimed a retry on both paths, including the one that
                # gives up - a misleading log in precisely the situation
                # someone is reading the log to understand.
                logger.warning(
                    "llm rate limited: assistant=%s attempt=%d retry_after=%s - %s",
                    self._assistant_id,
                    attempt,
                    exc.retry_after_seconds,
                    "retrying without retrieved context"
                    if can_shed
                    else "giving up on this turn",
                )

                if can_shed:
                    drop_context_to_fit = True

                    continue

                await self._give_up_on_turn()

                return
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

                # Before anything else, because the caller hears this and does
                # not read it: a table's pipes, a heading's hashes and a bold
                # marker's asterisks are all read out loud by the TTS provider
                # otherwise. Reported from a real call, where a pricing answer
                # came back as a markdown table.
                sentence = to_spoken_text(sentence)

                if not sentence:
                    # The chunk was pure markup - a table's separator row, say.
                    # There is nothing to say and nothing to check.
                    return True

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
            self._record_token_cost(generation)
            await self.push_frame(
                OutputTransportMessageUrgentFrame(
                    message={"type": "llm_complete", "text": full_reply}
                )
            )
            self._session_resilience.record_turn_succeeded()
        except LLMProviderError:
            await self._give_up_on_turn()

    def _record_token_cost(self, generation: int) -> None:
        """
        Attach what this turn's LLM call actually cost, as the provider
        reported it (item 25b).

        Read here, at the end of the turn, because that is the only point at
        which the answer exists: both SDKs deliver usage as trailing stream
        metadata (see `LLMProvider.last_usage`). A provider that reports
        nothing, or a model with no configured price, leaves the fields null
        rather than zero - "free" and "unknown" must not look the same in
        the billing data.

        Never allowed to break a turn. The caller has already heard the
        reply by this point; a bug in an accounting field must not turn a
        successful answer into a failed one, and `last_usage` is an optional
        part of the provider protocol that a third-party or older
        implementation may simply not have.
        """

        try:
            last_usage = getattr(self._llm_provider, "last_usage", None)
            usage = last_usage() if callable(last_usage) else None
            cost = realtime_turn_cost_micro_usd(usage)

            self._turn_metrics.record_token_cost(
                generation,
                prompt_tokens=usage.prompt_tokens if usage else None,
                completion_tokens=usage.completion_tokens if usage else None,
                cost_micro_usd=cost,
            )

            if usage is not None:
                logger.info(
                    "turn cost: assistant=%s prompt_tokens=%d completion_tokens=%d "
                    "cost_micro_usd=%s",
                    self._assistant_id,
                    usage.prompt_tokens,
                    usage.completion_tokens,
                    "unpriced" if cost is None else cost,
                )
        except Exception:
            logger.exception("could not record this turn's token cost")

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
    """
    Either of the two messages TTSProcessor speaks on the assistant's
    behalf without a turn behind them.

    "hearing_trouble" is the recoverable one: the transcriber has gone
    deaf and the caller is told so and invited to repeat themselves.
    "session_failover" is the graver one. Neither ends the call.
    """

    return isinstance(message, dict) and message.get("type") in (
        "session_failover",
        "hearing_trouble",
    )


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
        # The room is not an interruption. A transcriber will make words
        # out of a television or a passing conversation, and acting on those
        # cancels the reply - the assistant stopping mid-sentence at a noise.
        # So a mid-reply transcript only counts if the VAD heard the caller
        # themselves recently; see BARGE_IN_SPEECH_WINDOW_SECONDS for why a
        # window rather than "right now".
        since_speech = self._turn_detector.seconds_since_speech()

        if (
            since_speech is None
            or since_speech > config.BARGE_IN_SPEECH_WINDOW_SECONDS
        ):
            logger.info(
                "barge-in candidate ignored, the vad has not heard the caller: "
                "assistant=%s words=%d since_speech=%s",
                self._assistant_id,
                len(_normalized_words(text)),
                "never" if since_speech is None else f"{since_speech:.1f}s",
            )

            return

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
    call_context: CallContext | None = None,
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
    rows, since Call (build-plan item 28) doesn't exist yet.

    call_context (item 25b) is that same call's logging identity, already
    bound by the caller before this function runs - which is the part that
    matters, because Pipecat's processors capture the ambient context when
    their tasks are created here, and a binding made afterwards would reach
    none of them. Passing it in as well lets the metrics recorder advance
    the turn on it, so every log line carries the turn it belongs to.
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

    turn_metrics = TurnMetricsRecorder(call_id=call_id, call_context=call_context)
    turn_detection_processor = TurnDetectionProcessor(turn_detector, turn_metrics)
    session_resilience = SessionResilienceTracker(
        max_consecutive_failures=config.MAX_CONSECUTIVE_LLM_FAILURES
    )

    pipeline = Pipeline(
        [
            transport.input(),
            SpeechToTextProcessor(
                provider,
                language=language,
                keywords=keywords,
                # The same number the local VAD below uses. Two turn
                # detectors disagreeing means the slower one decides, and
                # the provider's own default was always the slower one - so
                # the operator's turn-sensitivity setting had no effect on
                # when a turn actually ended.
                silence_threshold_secs=sensitivity_to_stop_secs(sensitivity),
            ),
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
    worker = PipelineWorker(pipeline, enable_rtvi=False, idle_timeout_secs=None)

    @transport.event_handler("on_client_disconnected")
    async def _on_client_disconnected(_transport, _client) -> None:
        """
        End the session when the caller's socket closes.

        Nothing else did. A browser closing its connection delivers neither
        an EndFrame nor a CancelFrame, and the runner only stops on an
        external signal, so the pipeline stayed up with nobody on it and the
        speech-to-text reconnect loop went on dialling the provider forever.

        Every test call left one running. 126 were found looping at once,
        1,639 reconnect attempts in an hour between them, which rate-limited
        the speech provider and the LLM and left genuinely new calls with no
        transcription and no reply at all.

        This is the caller ending the call, which is the one thing that has
        always been allowed to end it - distinct from a provider failing
        mid-call, where reconnecting indefinitely remains correct.
        """

        logger.info("caller disconnected - ending the session")

        await worker.cancel()

    return worker
