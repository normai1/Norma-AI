"""
Deterministic speech provider mocks. No network, no real audio processing -
these exist so the test suite, and later item 22's replay harness, never
depend on a paid or live external API.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence

from app.providers.speech import SpeechProviderError, TranscriptEvent, Voice


class MockSTT:
    """
    Yields a caller-scripted sequence of transcript events, optionally with a
    per-event delay and/or a failure raised after the script is exhausted.
    """

    def __init__(
        self,
        *,
        script: Sequence[TranscriptEvent] = (),
        event_delay_seconds: float = 0.0,
        failure: SpeechProviderError | None = None,
    ) -> None:
        self._script = list(script)
        self._event_delay_seconds = event_delay_seconds
        self._failure = failure

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language: str,
        keywords: Sequence[str] = (),
    ) -> AsyncIterator[TranscriptEvent]:
        # The scripted transcript does not depend on the audio content, but
        # draining the iterator matches a real provider's contract: the
        # caller is streaming audio in, not just waiting on output.
        async for _ in audio:
            pass

        for event in self._script:
            if self._event_delay_seconds:
                await asyncio.sleep(self._event_delay_seconds)

            yield event

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

        if self._time_to_first_byte_seconds:
            await asyncio.sleep(self._time_to_first_byte_seconds)

        remaining = total_bytes

        try:
            while remaining > 0:
                chunk_bytes = min(self._chunk_size_bytes, remaining)

                yield b"\x00" * chunk_bytes

                remaining -= chunk_bytes
        except GeneratorExit:
            self.cancelled = True

            raise

    async def list_voices(self) -> Sequence[Voice]:
        if self._failure is not None:
            raise self._failure

        return list(self._voices)
