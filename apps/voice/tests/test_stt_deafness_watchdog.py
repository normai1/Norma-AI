"""
A speech-to-text stream that stays open and says nothing must be recovered
from, not endured.

Reported as "assistant not working". Two sessions in a row where the
caller's audio arrived for the full thirty-four seconds - several two-second
windows of it at clear speech level, peaks of 0.13, 0.14, 0.22 and 0.28 of
full scale - and the provider stream produced no transcript, no error, and
never closed. Every recovery path in SpeechToTextProcessor is driven by the
provider saying something; a stream that says nothing at all reached none of
them, so the log held one "stt stream starting" line and then nothing until
the caller hung up.
"""

import asyncio

import pytest
from norma_shared.speech import TranscriptEvent
from pipecat.frames.frames import (
    CancelFrame,
    InputAudioRawFrame,
    StartFrame,
)
from pipecat.clocks.system_clock import SystemClock
from pipecat.processors.frame_processor import FrameDirection, FrameProcessorSetup
from pipecat.utils.asyncio.task_manager import TaskManager

from app import config
from app.media_session import SpeechToTextProcessor

# One 20ms frame at 16kHz, at a peak the level meter reads as speech (well
# above the 0.08 threshold) and one it reads as an idle microphone.
_SPEECH = b"\x00\x40" * 160
_ROOM_NOISE = b"\x40\x00" * 160


class _SilentStream:
    """
    Connects, accepts audio forever, and never yields a transcript - the
    provider behaviour that had no recovery path.
    """

    def __init__(self) -> None:
        self.streams = 0

    async def stream(self, audio, *, language, keywords=()):
        self.streams += 1

        # Drain audio the way a real provider does, so the queue does not
        # simply back up, and yield nothing at all.
        async for _chunk in audio:
            pass

        return
        yield  # pragma: no cover - makes this an async generator


class _TalkativeStream:
    """Answers every chunk, so the watchdog must leave it alone."""

    def __init__(self) -> None:
        self.streams = 0

    async def stream(self, audio, *, language, keywords=()):
        self.streams += 1

        async for _chunk in audio:
            yield TranscriptEvent(text="hello", is_final=False)


class _Collector:
    def __init__(self) -> None:
        self.frames: list[object] = []

    async def __call__(self, frame, direction=None) -> None:
        self.frames.append(frame)


@pytest.fixture(autouse=True)
def _fast_watchdog(monkeypatch: pytest.MonkeyPatch):
    """The real windows are 12s/2s; the logic is identical at 1/50th."""

    monkeypatch.setattr(config, "STT_DEAF_WATCHDOG_SECONDS", 0.24)
    monkeypatch.setattr(config, "STT_DEAF_WATCHDOG_POLL_SECONDS", 0.02)
    monkeypatch.setattr(config, "STT_RECONNECT_DELAY_SECONDS", 0.0)
    monkeypatch.setattr(config, "MAX_STT_RECONNECT_DELAY_SECONDS", 0.0)


async def _make_processor(provider) -> SpeechToTextProcessor:
    """
    A processor wired up enough to own tasks. create_task/cancel_task go
    through pipecat's task manager, which a pipeline would normally install.
    """

    processor = SpeechToTextProcessor(provider, language="en")
    processor.push_frame = _Collector()

    task_manager = TaskManager()
    await processor.setup(
        FrameProcessorSetup(
            clock=SystemClock(), task_manager=task_manager, pipeline_worker=None
        )
    )

    return processor


async def _run(processor: SpeechToTextProcessor, frames: bytes, *, seconds: float):
    """Feed `frames` steadily for `seconds`, then cancel the session."""

    await processor.process_frame(StartFrame(), FrameDirection.DOWNSTREAM)

    deadline = asyncio.get_running_loop().time() + seconds

    while asyncio.get_running_loop().time() < deadline:
        await processor.process_frame(
            InputAudioRawFrame(audio=frames, sample_rate=16000, num_channels=1),
            FrameDirection.DOWNSTREAM,
        )
        await asyncio.sleep(0.01)

    await processor.process_frame(CancelFrame(), FrameDirection.DOWNSTREAM)
    await processor.cleanup()


async def test_a_stream_that_hears_speech_and_says_nothing_is_restarted() -> None:
    provider = _SilentStream()
    processor = await _make_processor(provider)

    await _run(processor, _SPEECH, seconds=1.0)

    # Without the watchdog this is exactly 1 - one stream, open and deaf,
    # for the whole call.
    assert provider.streams > 1


async def test_a_quiet_caller_is_never_mistaken_for_a_deaf_stream() -> None:
    """
    The failure mode to avoid: tearing down a perfectly healthy stream
    because the caller was thinking. Room noise is not speech, so nothing
    is owed a transcript and nothing should be restarted.
    """

    provider = _SilentStream()
    processor = await _make_processor(provider)

    await _run(processor, _ROOM_NOISE, seconds=1.0)

    assert provider.streams == 1


async def test_a_stream_that_is_transcribing_is_left_alone() -> None:
    provider = _TalkativeStream()
    processor = await _make_processor(provider)

    await _run(processor, _SPEECH, seconds=1.0)

    assert provider.streams == 1


async def test_the_restart_is_visible_in_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """
    The original defect was as much an observability one as a behavioural
    one: nothing in the log distinguished a deaf stream from a caller who
    never spoke.
    """

    import logging

    provider = _SilentStream()
    processor = await _make_processor(provider)

    with caplog.at_level(logging.WARNING):
        await _run(processor, _SPEECH, seconds=1.0)

    assert any("returned nothing" in record.getMessage() for record in caplog.records)


async def test_the_caller_s_words_are_never_logged_by_the_watchdog(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    provider = _SilentStream()
    processor = await _make_processor(provider)

    with caplog.at_level(logging.DEBUG):
        await _run(processor, _SPEECH, seconds=0.6)

    for record in caplog.records:
        message = record.getMessage()
        assert "audio_base_64" not in message
        assert _SPEECH.hex()[:16] not in message


async def test_the_watchdog_stops_when_the_call_does() -> None:
    """
    A watchdog left running after cleanup is the same class of leak as the
    reconnect loop that once left 126 zombie sessions behind it.
    """

    provider = _SilentStream()
    processor = await _make_processor(provider)

    await _run(processor, _SPEECH, seconds=0.4)

    assert processor._watchdog_task is None
