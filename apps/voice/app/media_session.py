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
from norma_shared.speech import SpeechToTextProvider
from pipecat.audio.vad.vad_analyzer import VADAnalyzer
from pipecat.frames.frames import (
    CancelFrame,
    EndFrame,
    Frame,
    InputAudioRawFrame,
    OutputAudioRawFrame,
    OutputTransportMessageUrgentFrame,
    StartFrame,
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
from app.turn_detection import TurnDetector

# A fixed, generic apology - never str(exception). CLAUDE.md's rule against
# exposing internal error details to a user applies to this JSON fallback
# exactly as much as to an HTTP error response.
_LLM_ERROR_MESSAGE = "Sorry, I'm having trouble responding right now."

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
    (transcripts, for now) serializes out as a JSON text message instead
    of audio - the only way to observe a transcript at all before an LLM/
    TTS stage exists to turn it into a spoken reply.

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
        await self._maybe_emit_turn_ended()

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

    Only one LLM turn ever runs at a time, by construction rather than a
    race that needs winning: reset_for_next_turn() runs only in this
    processor's own finally block once a call finishes (success or error),
    and TurnDetector.turn_ended() stays latched True - so TurnDetectionProcessor
    cannot emit a second turn_ended - until that reset happens. The
    is-a-call-already-running check below is therefore a defensive
    invariant, not something reachable through the pipeline as currently
    wired; it costs nothing to keep and protects against a future change
    (item 20e's barge-in is the likely candidate) altering that sequencing.
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
        finally:
            self._turn_detector.reset_for_next_turn()


def build_voice_session_pipeline_worker(
    websocket: WebSocket,
    provider: SpeechToTextProvider,
    llm_provider: LLMProvider,
    *,
    assistant_id: uuid.UUID,
    language: str = "en",
    keywords: Sequence[str] = (),
    sensitivity: float = 0.5,
    system_prompt: str = "",
    creativity: float = 0.3,
    vad_analyzer: VADAnalyzer | None = None,
) -> PipelineWorker:
    """
    Wire one WebSocket connection into a Pipecat pipeline that transcribes
    incoming audio, detects when the caller's turn has ended, and streams
    an LLM reply - all as JSON messages back to the caller. This is the
    only Pipecat-specific construction in the media plane - later items add
    real stages to the Pipeline list here, not by reaching into Pipecat
    from elsewhere in the app.

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

    pipeline = Pipeline(
        [
            transport.input(),
            SpeechToTextProcessor(provider, language=language, keywords=keywords),
            TurnDetectionProcessor(turn_detector),
            LLMTurnProcessor(
                llm_provider,
                turn_detector,
                assistant_id=assistant_id,
                system_prompt=system_prompt,
                creativity=creativity,
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
