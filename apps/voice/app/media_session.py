"""
Norma's own wrapper around Pipecat's transport/pipeline construction (item
20a's "behind Norma's own interfaces" requirement, CLAUDE.md section 5.5).
This is explicitly spike code: a minimal echo pipeline that proves
bidirectional audio streaming genuinely works, not yet a conversation.
Application code should call build_echo_pipeline_worker() rather than
construct Pipecat primitives directly, so a future framework swap - or
20b-20g adding real pipeline stages - only touches this module.
"""

from fastapi import WebSocket
from pipecat.frames.frames import Frame, InputAudioRawFrame, OutputAudioRawFrame
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineWorker
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.serializers.base_serializer import FrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)

# Matches app/providers/speech.py's canonical internal audio format (item
# 9a) on the control plane - the media plane should speak the same format
# its speech providers already assume once 20b/20e wire them in.
AUDIO_SAMPLE_RATE_HZ = 16_000


class RawAudioFrameSerializer(FrameSerializer):
    """
    The simplest possible serializer: a binary WebSocket message is raw PCM
    audio bytes, nothing else - no protocol, no framing. Enough to prove
    bidirectional audio streaming; a real telephony provider gets its own
    serializer (Pipecat already ships one each for Twilio/Telnyx/Plivo/
    Vonage/Genesys, for item 23+ to use directly rather than reinventing).
    """

    def __init__(self, *, sample_rate: int, num_channels: int = 1) -> None:
        super().__init__()
        self._sample_rate = sample_rate
        self._num_channels = num_channels

    async def serialize(self, frame: Frame) -> bytes | None:
        if isinstance(frame, OutputAudioRawFrame):
            return frame.audio

        return None

    async def deserialize(self, data: str | bytes) -> Frame | None:
        if isinstance(data, bytes):
            return InputAudioRawFrame(
                audio=data,
                sample_rate=self._sample_rate,
                num_channels=self._num_channels,
            )

        return None


class EchoProcessor(FrameProcessor):
    """
    Converts every InputAudioRawFrame into an OutputAudioRawFrame carrying
    the same bytes and forwards everything else unchanged. The conversion
    is required, not cosmetic: the output transport only recognizes
    OutputAudioRawFrame, so forwarding the input frame's class unchanged
    would silently vanish rather than reach the caller. Proves the
    pipeline genuinely carries audio both ways; item 20b replaces this
    with real STT/LLM/TTS stages.
    """

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, InputAudioRawFrame):
            frame = OutputAudioRawFrame(
                audio=frame.audio,
                sample_rate=frame.sample_rate,
                num_channels=frame.num_channels,
            )

        await self.push_frame(frame, direction)


def build_echo_pipeline_worker(websocket: WebSocket) -> PipelineWorker:
    """
    Wire one WebSocket connection into a minimal Pipecat pipeline that
    echoes audio back to the caller. This is the only Pipecat-specific
    construction in the media plane - later items add real stages to the
    Pipeline list here, not by reaching into Pipecat from elsewhere in the
    app.
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

    pipeline = Pipeline([transport.input(), EchoProcessor(), transport.output()])

    return PipelineWorker(pipeline)
