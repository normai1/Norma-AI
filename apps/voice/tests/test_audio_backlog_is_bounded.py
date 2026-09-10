"""

Caller audio waiting to reach the speech provider is capped, because the

provider's own queue is not ours to overflow.


The failure this prevents, confirmed from ElevenLabs' own queue_overflow

message: hand the realtime API a burst of audio, and it says so once and

then stops transcribing for the life of that connection - no transcript, no

error, no close. Reported as "assistant not answering anything". Two ways a

burst arises here: a browser sending faster than realtime (measured at 149

and 165 frames in a two-second window, against 100 at realtime), and a

backlog accumulated while a stream was down being dumped into its

replacement.


CLAUDE.md section 5.1: no unbounded queues, backpressure must be explicit.

"""

import asyncio

import pytest

from app import media_session
from app.media_session import (
    _MAX_QUEUED_AUDIO_SECONDS,
    SpeechToTextProcessor,
)

_SAMPLE_RATE = 16000

# 320 samples of 16-bit mono at 16kHz = 20ms, so 50 frames is one

# second and the two-second cap is exactly 100 of them.

_FRAME = bytes([0x00, 0x40]) * 320  # 20ms of 16-bit mono at 16kHz
_FRAMES_PER_SECOND = 50


class _NeverConsumingSTT:
    """A provider that accepts the stream but never reads a single chunk."""

    async def stream(
        self, audio, *, language, keywords=(), silence_threshold_secs=None
    ):

        await asyncio.sleep(3600)

        return

        yield  # pragma: no cover - makes this an async generator


async def _swallow(frame, direction=None) -> None:

    pass


def _processor() -> SpeechToTextProcessor:

    processor = SpeechToTextProcessor(_NeverConsumingSTT(), language="en")

    processor.push_frame = _swallow

    return processor


def _feed(processor: SpeechToTextProcessor, frames: int) -> None:

    for _ in range(frames):
        processor._queue_audio(_FRAME, sample_rate=_SAMPLE_RATE)


def test_a_backlog_never_grows_past_the_cap() -> None:

    processor = _processor()

    _feed(processor, 10 * _FRAMES_PER_SECOND)  # ten seconds of audio

    cap_bytes = int(_MAX_QUEUED_AUDIO_SECONDS * _SAMPLE_RATE * 2)

    assert processor._queued_bytes <= cap_bytes

    assert processor._audio_queue.qsize() <= cap_bytes // len(_FRAME)


def test_the_audio_kept_is_the_newest() -> None:
    """

    Dropping the oldest rather than refusing the newest. Audio from several

    seconds ago cannot help a live call - by the time it were transcribed

    the caller has moved on - and keeping it only makes the burst that kills

    the next stream bigger.

    """

    processor = _processor()

    total = 10 * _FRAMES_PER_SECOND

    # Each frame is stamped with its own index, so which ones survived is

    # readable rather than inferred.

    for i in range(total):
        processor._queue_audio(i.to_bytes(2, "big") * 320, sample_rate=_SAMPLE_RATE)

    kept = []

    while not processor._audio_queue.empty():
        chunk = processor._audio_queue.get_nowait()

        kept.append(int.from_bytes(chunk[:2], "big"))

    # An unbroken run ending at the most recent frame.

    assert kept[-1] == total - 1

    assert kept == list(range(total - len(kept), total))


def test_an_ordinary_realtime_stream_is_never_dropped() -> None:
    """

    The cap must be invisible in normal operation. Two seconds of audio

    arriving while the provider keeps up must reach it intact - a cap that

    fired on healthy calls would be trading one silence for another.

    """

    processor = _processor()

    _feed(processor, int(_MAX_QUEUED_AUDIO_SECONDS * _FRAMES_PER_SECOND))

    assert processor._dropped_frames == 0


def test_dropping_is_logged(caplog: pytest.LogCaptureFixture) -> None:

    import logging

    processor = _processor()

    with caplog.at_level(logging.WARNING):
        _feed(processor, 10 * _FRAMES_PER_SECOND)

    assert any(
        "faster than the provider accepts" in r.getMessage() for r in caplog.records
    )


def test_dropping_is_summarised_rather_than_logged_per_frame(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """

    A burst drops one frame for every frame that arrives, so a line each

    produced 84 identical warnings inside one second on a real session -

    noise that buries the signal it exists to give.


    The first drop still reports at once, because "this is happening at all"

    is worth knowing immediately; everything after it is summarised on the

    audio-level report's own cadence, carrying the count that makes one line

    worth the hundreds it replaces.

    """

    import logging

    now = [1000.0]

    monkeypatch.setattr(media_session.time, "monotonic", lambda: now[0])

    processor = _processor()

    with caplog.at_level(logging.WARNING):
        _feed(processor, 5 * _FRAMES_PER_SECOND)

        first_burst = _drop_lines(caplog)

        now[0] += 10.0

        _feed(processor, 5 * _FRAMES_PER_SECOND)

        after_second_burst = _drop_lines(caplog)

    # 500 frames fed, 100 of them fit under the cap, so 400 were dropped -

    # and reported in two lines rather than four hundred.

    assert processor._dropped_frames == 400

    assert len(first_burst) == 1

    assert len(after_second_burst) == 2

    # The second line accounts for everything suppressed since the first.

    assert "150 frames" in after_second_burst[1]

    assert "151 this session" in after_second_burst[1]


def _drop_lines(caplog: pytest.LogCaptureFixture) -> list[str]:

    return [
        r.getMessage()
        for r in caplog.records
        if "faster than the provider accepts" in r.getMessage()
    ]


def test_the_end_of_stream_sentinel_survives_a_drop() -> None:
    """

    The sentinel is what ends the current provider stream. Dropping it would

    leave that stream running with nothing to end it - the exact deaf-stream

    state this whole area exists to avoid.

    """

    processor = _processor()

    _feed(processor, 10)

    processor._audio_queue.put_nowait(None)

    _feed(processor, 10 * _FRAMES_PER_SECOND)

    drained = []

    while not processor._audio_queue.empty():
        drained.append(processor._audio_queue.get_nowait())

    assert None in drained


async def test_consuming_audio_frees_the_backlog() -> None:
    """

    Without decrementing on the way out, the cap would ratchet shut and a

    healthy call would start dropping audio after its first two seconds.

    """

    processor = _processor()

    _feed(processor, 20)

    queued_before = processor._queued_bytes

    iterator = processor._audio_iterator()

    await anext(iterator)

    await anext(iterator)

    assert processor._queued_bytes == queued_before - 2 * len(_FRAME)

    await iterator.aclose()
