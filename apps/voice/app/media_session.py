"""
Norma's own wrapper around Pipecat's transport/pipeline construction (item
20a's "behind Norma's own interfaces" requirement, CLAUDE.md section 5.5).
Application code should call build_voice_session_pipeline_worker() rather
than construct Pipecat primitives directly, so a future framework swap -
or 20d-20g adding real pipeline stages - only touches this module.
"""

import asyncio
import json
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

from app.conversation import ConversationState, assemble_system_prompt
from app.llm import LLMProvider, LLMProviderError
from app.retrieval_client import fetch_retrieved_context
from app.sentence_chunker import SentenceChunker
from app.turn_detection import TurnDetector

# Fixed, generic apologies - never str(exception). CLAUDE.md's rule against
# exposing internal error details to a user applies to these JSON fallbacks
# exactly as much as to an HTTP error response.
_LLM_ERROR_MESSAGE = "Sorry, I'm having trouble responding right now."
_TTS_ERROR_MESSAGE = "Sorry, I'm having trouble speaking right now."

# Matches norma_shared/speech.py's canonical internal audio format (item
# 9a) on the control plane - the media plane should speak the same format
# its speech providers already assume.
AUDIO_SAMPLE_RATE_HZ = 16_000


class RawAudioFrameSerializer(FrameSerializer):
    """
    A binary WebSocket message is raw PCM audio bytes in, nothing else - no
    protocol, no framing. Enough to prove bidirectional audio streaming; a
    real telephony provider gets its own serializer (Pipecat already ships
    one each for Twilio/Telnyx/Plivo/Vonage/Genesys, for item 23+ to use
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

    async def _audio_iterator(self) -> AsyncIterator[bytes]:
        while True:
            chunk = await self._audio_queue.get()

            if chunk is None:
                return

            yield chunk

    async def _run_stream(self) -> None:
        async for event in self._provider.stream(
            self._audio_iterator(),
            language=self._language,
            keywords=self._keywords,
        ):
            await self.push_frame(
                OutputTransportMessageUrgentFrame(
                    message={
                        "type": "transcript",
                        "text": event.text,
                        "is_final": event.is_final,
                    }
                )
            )

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._stream_task = self.create_task(self._run_stream())
            await self.push_frame(frame, direction)
        elif isinstance(frame, InputAudioRawFrame):
            await self._audio_queue.put(frame.audio)
            await self.push_frame(frame, direction)
        elif isinstance(frame, (EndFrame, CancelFrame)):
            await self._audio_queue.put(None)
            await self.push_frame(frame, direction)
        else:
            await self.push_frame(frame, direction)


def _is_transcript_message(message: object) -> bool:
    return isinstance(message, dict) and message.get("type") == "transcript"


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

    def __init__(self, turn_detector: TurnDetector) -> None:
        super().__init__()
        self._turn_detector = turn_detector
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
    """

    def __init__(
        self,
        llm_provider: LLMProvider,
        turn_detector: TurnDetector,
        *,
        assistant_id: uuid.UUID,
        system_prompt: str,
        creativity: float,
    ) -> None:
        super().__init__()
        self._llm_provider = llm_provider
        self._turn_detector = turn_detector
        self._assistant_id = assistant_id
        self._system_prompt = system_prompt
        self._creativity = creativity
        self._conversation = ConversationState()
        self._llm_task: asyncio.Task | None = None

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, OutputTransportMessageUrgentFrame) and _is_turn_ended_message(
            frame.message
        ):
            if self._llm_task is None or self._llm_task.done():
                self._llm_task = self.create_task(
                    self._run_llm_turn(frame.message["text"])
                )
        elif isinstance(
            frame, OutputTransportMessageUrgentFrame
        ) and _is_caller_speech_started_message(frame.message):
            if self._llm_task is not None:
                self._llm_task.cancel()
        elif isinstance(frame, (EndFrame, CancelFrame)) and self._llm_task is not None:
            self._llm_task.cancel()

        await self.push_frame(frame, direction)

    async def _run_llm_turn(self, caller_text: str) -> None:
        try:
            self._conversation.append_user_turn(caller_text)
            retrieved_context = await fetch_retrieved_context(
                self._assistant_id, caller_text
            )
            system = assemble_system_prompt(
                base_prompt=self._system_prompt, retrieved_context=retrieved_context
            )

            reply_parts: list[str] = []

            async for delta in self._llm_provider.stream(
                self._conversation.messages, system=system, temperature=self._creativity
            ):
                reply_parts.append(delta)
                await self.push_frame(
                    OutputTransportMessageUrgentFrame(
                        message={"type": "llm_delta", "text": delta}
                    )
                )

            full_reply = "".join(reply_parts)
            self._conversation.append_assistant_turn(full_reply)
            await self.push_frame(
                OutputTransportMessageUrgentFrame(
                    message={"type": "llm_complete", "text": full_reply}
                )
            )
        except LLMProviderError:
            await self.push_frame(
                OutputTransportMessageUrgentFrame(
                    message={"type": "llm_error", "text": _LLM_ERROR_MESSAGE}
                )
            )


def _is_llm_delta_message(message: object) -> bool:
    return isinstance(message, dict) and message.get("type") == "llm_delta"


def _is_llm_reply_finished_message(message: object) -> bool:
    return isinstance(message, dict) and message.get("type") in ("llm_complete", "llm_error")


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
        *,
        voice_id: str,
        speech_rate: float,
    ) -> None:
        super().__init__()
        self._tts_provider = tts_provider
        self._turn_detector = turn_detector
        self._turn_detection_processor = turn_detection_processor
        self._voice_id = voice_id
        self._speech_rate = speech_rate
        self._chunker = SentenceChunker()
        self._sentence_queue: asyncio.Queue[str] = asyncio.Queue()
        self._player_task: asyncio.Task | None = None
        self._current_playback: asyncio.Task | None = None
        self._llm_finished = False
        self._reply_in_progress = False

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, StartFrame):
            self._player_task = self.create_task(self._play_sentences())
        elif isinstance(frame, OutputTransportMessageUrgentFrame) and _is_turn_ended_message(
            frame.message
        ):
            self._reply_in_progress = True
            self._llm_finished = False
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
        elif isinstance(frame, (EndFrame, CancelFrame)) and self._player_task is not None:
            self._player_task.cancel()

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

        if not self._reply_in_progress:
            return

        while not self._sentence_queue.empty():
            self._sentence_queue.get_nowait()

        if self._current_playback is not None:
            self._current_playback.cancel()

        self._chunker.reset()
        self._llm_finished = False
        self._reply_in_progress = False
        await self._reset_turn()

    async def _maybe_reset_after_reply(self) -> None:
        if (
            self._llm_finished
            and self._sentence_queue.empty()
            and self._current_playback is None
        ):
            self._reply_in_progress = False
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
        await self._turn_detection_processor.recheck()

        await self.push_frame(
            OutputTransportMessageUrgentFrame(message={"type": "reply_finished"})
        )

    async def _play_sentences(self) -> None:
        while True:
            sentence = await self._sentence_queue.get()

            self._current_playback = self.create_task(self._speak(sentence))

            try:
                await self._current_playback
            except asyncio.CancelledError:
                pass
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

    async def _speak(self, sentence: str) -> None:
        try:
            async for chunk in self._tts_provider.synthesize(
                sentence, voice_id=self._voice_id, speed=self._speech_rate
            ):
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
    language: str = "en",
    keywords: Sequence[str] = (),
    sensitivity: float = 0.5,
    system_prompt: str = "",
    creativity: float = 0.3,
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

    turn_detection_processor = TurnDetectionProcessor(turn_detector)

    pipeline = Pipeline(
        [
            transport.input(),
            SpeechToTextProcessor(provider, language=language, keywords=keywords),
            turn_detection_processor,
            LLMTurnProcessor(
                llm_provider,
                turn_detector,
                assistant_id=assistant_id,
                system_prompt=system_prompt,
                creativity=creativity,
            ),
            TTSProcessor(
                tts_provider,
                turn_detector,
                turn_detection_processor,
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
    return PipelineWorker(pipeline, enable_rtvi=False)
