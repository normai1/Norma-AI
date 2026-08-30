"""
Deterministic speech provider mocks. No network, no real audio processing -
these exist so the test suite, and later item 22's replay harness, never
depend on a paid or live external API.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence

from norma_shared.speech import SpeechProviderError, TranscriptEvent, Voice


class MockSTT:
    """
    Yields a caller-scripted sequence of transcript events, optionally with a
    per-event delay and/or a failure raised after the script is exhausted.

    By default (chunks_before_event=None) drains the whole audio iterator
    before yielding anything - fine for tests that only care about the
    final script content, but unable to express a partial transcript
    arriving mid-stream (finding F-40). Passing chunks_before_event - one
    integer per script entry, "consume this many audio chunks before
    yielding this event" - switches to interleaved mode instead, so a
    test can prove ordering between audio arrival and transcript events.
    """

    def __init__(
        self,
        *,
        script: Sequence[TranscriptEvent] = (),
        chunks_before_event: Sequence[int] | None = None,
        event_delay_seconds: float = 0.0,
        failure: SpeechProviderError | None = None,
    ) -> None:
        self._script = list(script)
        self._chunks_before_event = (
            list(chunks_before_event) if chunks_before_event is not None else None
        )
        self._event_delay_seconds = event_delay_seconds
        self._failure = failure
        # Records the keywords argument of the most recent stream() call, for
        # a test to assert glossary terms actually reached the provider -
        # mirrors MockEmbeddingProvider.embedded_texts's exact precedent.
        self.received_keywords: list[str] | None = None

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language: str,
        keywords: Sequence[str] = (),
    ) -> AsyncIterator[TranscriptEvent]:
        self.received_keywords = list(keywords)

        if self._chunks_before_event is None:
            # The scripted transcript does not depend on the audio content,
            # but draining the iterator matches a real provider's contract:
            # the caller is streaming audio in, not just waiting on output.
            async for _ in audio:
                pass

            for event in self._script:
                if self._event_delay_seconds:
                    await asyncio.sleep(self._event_delay_seconds)

                yield event

            if self._failure is not None:
                raise self._failure

            return

        audio_iterator = audio.__aiter__()
        consumed = 0

        for event, chunks_needed in zip(
            self._script, self._chunks_before_event, strict=True
        ):
            while consumed < chunks_needed:
                try:
                    await audio_iterator.__anext__()
                except StopAsyncIteration:
                    break

                consumed += 1

            if self._event_delay_seconds:
                await asyncio.sleep(self._event_delay_seconds)

            yield event

        async for _ in audio_iterator:
            pass

        if self._failure is not None:
            raise self._failure


class MockTTS:
    """
    Synthesizes deterministic silent audio whose length is proportional to
    the input text, streamed in fixed-size chunks. Records whether a
    synthesis was cancelled mid-stream - the property item 20e's barge-in
    tests assert on.
    """

    def __init__(
        self,
        *,
        voices: Sequence[Voice] = (),
        bytes_per_character: int = 320,
        chunk_size_bytes: int = 3_200,
        time_to_first_byte_seconds: float = 0.0,
        failure: SpeechProviderError | None = None,
    ) -> None:
        self._voices = list(voices)
        self._bytes_per_character = bytes_per_character
        self._chunk_size_bytes = chunk_size_bytes
        self._time_to_first_byte_seconds = time_to_first_byte_seconds
        self._failure = failure
        self.cancelled = False

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        speed: float = 1.0,
    ) -> AsyncIterator[bytes]:
        if self._failure is not None:
            raise self._failure

        total_bytes = len(text) * self._bytes_per_character

        if total_bytes == 0:
            return

        try:
            if self._time_to_first_byte_seconds:
                await asyncio.sleep(self._time_to_first_byte_seconds)

            remaining = total_bytes

            while remaining > 0:
                chunk_bytes = min(self._chunk_size_bytes, remaining)

                yield b"\x00" * chunk_bytes

                remaining -= chunk_bytes
        except (GeneratorExit, asyncio.CancelledError):
            # Real barge-in cancellation (item 20e) cancels the asyncio
            # Task consuming this generator, not this generator's own
            # aclose() - verified empirically that a cancelled consuming
            # task delivers CancelledError here, never GeneratorExit.
            # Catching only GeneratorExit (as this method originally did)
            # would silently never set .cancelled for that real
            # cancellation path, breaking the barge-in tests this
            # property's docstring says it exists for. The
            # time_to_first_byte_seconds sleep is inside this try too -
            # cancellation before any chunk is ever yielded (exactly
            # barge-in's most important case) must still be caught.
            self.cancelled = True

            raise

    async def list_voices(self) -> Sequence[Voice]:
        if self._failure is not None:
            raise self._failure

        return list(self._voices)
