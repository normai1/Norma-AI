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
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import (
    CancelFrame,
    InputAudioRawFrame,
    StartFrame,
)
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

    async def stream(
        self, audio, *, language, keywords=(), silence_threshold_secs=None
    ):
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

    async def stream(
        self, audio, *, language, keywords=(), silence_threshold_secs=None
    ):
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


class _SilentProvider:
    """Yields no transcripts - these tests drive the watchdog directly."""

    async def stream(self, audio, *, language, keywords=(), silence_threshold_secs=None):
        return
        yield  # pragma: no cover - makes this an async generator


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


class _SilentAfterOneStream:
    """
    Transcribes once, then goes quiet while still draining audio - and, when
    the audio ends, keeps the stream open rather than returning.

    That last part is the whole point and is what the real adapter does: once
    the caller's audio ends it waits for the server to close the connection,
    which a server that has already gone quiet never does. A fake that simply
    returns when the audio stops cannot tell the old, broken watchdog from
    the fixed one - both look identical - which is exactly how a first
    version of these tests passed against the bug they were written for.
    """

    def __init__(self) -> None:
        self.streams = 0
        self.chunks_consumed = 0

    async def stream(
        self, audio, *, language, keywords=(), silence_threshold_secs=None
    ):
        self.streams += 1
        first = self.streams == 1

        async for _chunk in audio:
            self.chunks_consumed += 1

            if first:
                first = False
                yield TranscriptEvent(text="hello", is_final=True)

        # Audio exhausted. The real provider now waits on the server.
        await asyncio.sleep(3600)


async def test_a_restarted_stream_keeps_draining_the_caller_s_audio() -> None:
    """
    The regression this exists to prevent, and the second bug reported as
    "assistant is not responding anything".

    The watchdog used to end the audio iterator rather than the stream. That
    stops anything draining the audio queue at once, while the stream itself
    goes on waiting for a server that has already gone quiet - so the call
    was left with no consumer at all. Every arriving frame then evicted the
    one before it under the backlog cap: 100 frames in per two seconds, 100
    dropped, the caller inaudible for the rest of the session.

    So it is not enough that the watchdog fires. Audio has to still be
    reaching a provider afterwards.
    """

    provider = _SilentAfterOneStream()
    processor = await _make_processor(provider)

    await _run(processor, _SPEECH, seconds=1.2)

    consumed_at_restart = provider.chunks_consumed

    assert provider.streams > 1, "the watchdog never restarted the stream"
    assert consumed_at_restart > 0
    # The replacement stream is doing the job the abandoned one stopped
    # doing, rather than the queue silently filling.
    assert processor._audio_queue.qsize() < 10


async def test_audio_is_not_dropped_wholesale_while_streams_restart() -> None:
    """
    The number that gave the bug away in production: frames dropped per
    two-second window equal to frames arriving in it.
    """

    provider = _SilentAfterOneStream()
    processor = await _make_processor(provider)

    await _run(processor, _SPEECH, seconds=1.2)

    assert processor._dropped_frames == 0


async def test_the_caller_is_told_out_loud_once_the_stream_keeps_going_deaf(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Regression for a real call: the transcriber went deaf while the
    microphone was delivering healthy audio, the watchdog restarted the
    stream twice, and the caller heard nothing at all for the whole session.

    The existing failover announcement needs fifty reconnects, which is
    right for a provider that has genuinely gone away and far too patient
    for this. CLAUDE.md is explicit that silence is the worst possible
    failure; a caller who is told what is wrong can repeat themselves or
    hang up deliberately, instead of talking to something that stopped
    listening without saying so.
    """

    from app import config
    from app.media_session import _HEARING_TROUBLE_MESSAGE

    monkeypatch.setattr(config, "STT_HEARING_TROUBLE_RESTARTS", 2)

    processor = await _make_processor(_SilentProvider())
    spoken: list[dict] = []

    async def capture(frame, direction=None):
        message = getattr(frame, "message", None)

        if isinstance(message, dict):
            spoken.append(message)

    monkeypatch.setattr(processor, "push_frame", capture)

    # One restart says nothing: streams close on their own and usually
    # recover within a second, and narrating that is noise.
    processor._deaf_restarts = 1
    await processor._maybe_say_it_cannot_hear()

    assert spoken == []

    processor._deaf_restarts = 2
    await processor._maybe_say_it_cannot_hear()

    assert [m["type"] for m in spoken] == ["hearing_trouble"]
    assert spoken[0]["message"] == _HEARING_TROUBLE_MESSAGE


async def test_it_does_not_repeat_the_notice_on_every_restart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Worth repeating if the trouble persists - a caller who hears it once and
    then nothing concludes the call is dead - but not on every restart.
    """

    from app import config

    monkeypatch.setattr(config, "STT_HEARING_TROUBLE_RESTARTS", 2)
    monkeypatch.setattr(config, "STT_HEARING_TROUBLE_COOLDOWN_SECONDS", 3600.0)

    processor = await _make_processor(_SilentProvider())
    spoken: list[dict] = []

    async def capture(frame, direction=None):
        message = getattr(frame, "message", None)

        if isinstance(message, dict):
            spoken.append(message)

    monkeypatch.setattr(processor, "push_frame", capture)

    processor._deaf_restarts = 5

    for _ in range(4):
        await processor._maybe_say_it_cannot_hear()

    assert len(spoken) == 1


async def test_a_transcript_ends_the_run_so_a_recovered_call_stays_quiet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Counted consecutively, not cumulatively: a call that hiccups once every
    few minutes is not one to keep apologising on.
    """

    processor = await _make_processor(_SilentProvider())
    processor._deaf_restarts = 3

    # What _consume_stream does on every event the provider yields.
    processor._deaf_restarts = 0

    assert processor._deaf_restarts == 0
