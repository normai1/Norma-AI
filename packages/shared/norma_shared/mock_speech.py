"""
Deterministic speech provider mocks. No network, no real audio processing -
these exist so the test suite, and later item 22's replay harness, never
depend on a paid or live external API.
"""

import asyncio
import time
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

    A scripted failure is only ever raised after the audio iterator is
    fully drained (the caller has stopped sending audio), matching a real
    provider's contract - but that makes it unusable for testing a
    provider crash *while the caller is still actively talking* (item
    20g's session-failover path), since a live connection's audio never
    ends on its own. fail_without_draining opts out of that drain for
    exactly that case.
    """

    def __init__(
        self,
        *,
        script: Sequence[TranscriptEvent] = (),
        chunks_before_event: Sequence[int] | None = None,
        event_delay_seconds: float = 0.0,
        failure: SpeechProviderError | None = None,
        fail_without_draining: bool = False,
        fail_times: int | None = None,
        silent_closes: int = 0,
        close_after_script: bool = False,
    ) -> None:
        self._script = list(script)
        self._chunks_before_event = (
            list(chunks_before_event) if chunks_before_event is not None else None
        )
        self._event_delay_seconds = event_delay_seconds
        self._failure = failure
        self._fail_without_draining = fail_without_draining
        # None (the default) means every stream() call raises `failure`,
        # matching this class's original all-or-nothing behavior exactly.
        # An int caps that to only the first fail_times calls, after which
        # stream() succeeds normally - lets a test prove a caller's
        # reconnect/retry logic actually recovers, not just that it
        # eventually gives up (that path is already covered by leaving
        # this None).
        self._fail_times = fail_times
        # How many of the first stream() calls end immediately, cleanly,
        # having yielded nothing - a real provider behavior (observed
        # against ElevenLabs' realtime STT closing a second or two into a
        # live session) that raises no error at all, and so is invisible to
        # any retry keyed on exceptions.
        self._silent_closes = silent_closes
        # Whether the stream ends as soon as its script runs out, instead of
        # draining audio until the call itself ends. Models a live provider
        # hanging up mid-call - see _stay_open.
        self._close_after_script = close_after_script
        # How many times stream() has been called - a test asserts on this
        # directly to prove a reconnect attempt actually happened, mirroring
        # MockTTS.call_count's exact precedent.
        self.call_count = 0
        # Records the keywords argument of the most recent stream() call, for
        # a test to assert glossary terms actually reached the provider -
        # mirrors MockEmbeddingProvider.embedded_texts's exact precedent.
        self.received_keywords: list[str] | None = None

    async def _stay_open(self) -> None:
        """
        By default the stream drains audio until the call itself ends and
        then returns - what every consumer of this mock expects, and what
        makes its end mean "the call is over" rather than "the provider
        hung up".

        close_after_script=True skips that drain, so the stream ends the
        moment the script runs out while the call is still live - modelling
        a provider that hangs up mid-call, the condition
        SpeechToTextProcessor reconnects on. Opt-in deliberately: an
        earlier attempt made never-ending the default and hung every test
        that simply iterates stream() to completion.
        """

        return

    async def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language: str,
        keywords: Sequence[str] = (),
    ) -> AsyncIterator[TranscriptEvent]:
        self.call_count += 1
        self.received_keywords = list(keywords)

        if self.call_count <= self._silent_closes:
            return

        should_fail = self._failure is not None and (
            self._fail_times is None or self.call_count <= self._fail_times
        )

        if self._chunks_before_event is None:
            # The scripted transcript does not depend on the audio content,
            # but draining the iterator matches a real provider's contract:
            # the caller is streaming audio in, not just waiting on output.
            if not self._fail_without_draining and not self._close_after_script:
                async for _ in audio:
                    pass

            for event in self._script:
                if self._event_delay_seconds:
                    await asyncio.sleep(self._event_delay_seconds)

                yield event

            if should_fail:
                raise self._failure

            await self._stay_open()

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

        if not self._fail_without_draining and not self._close_after_script:
            async for _ in audio_iterator:
                pass

        if should_fail:
            raise self._failure

        await self._stay_open()


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
        # How many times synthesize() has been called - item 20g's retry
        # tests assert on this directly, mirroring MockLLM.call_count's
        # exact precedent.
        self.call_count = 0
        # monotonic() timestamp of each synthesize() call's start, in call
        # order - lets a test prove two calls overlapped (a prefetch
        # started before the prior one finished) by comparing the gap
        # between them against time_to_first_byte_seconds, without relying
        # on brittle absolute wall-clock thresholds elsewhere in the
        # pipeline (turn detection, worker startup, etc).
        self.call_started_at: list[float] = []
        # The previous_text handed to each synthesize() call, in call order
        # - lets a test prove prosody continuity actually reaches the
        # provider rather than existing only as an unused parameter,
        # mirroring MockSTT.received_keywords's exact precedent.
        self.received_previous_texts: list[str] = []

    async def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        speed: float = 1.0,
        previous_text: str = "",
    ) -> AsyncIterator[bytes]:
        self.call_count += 1
        self.call_started_at.append(time.monotonic())
        self.received_previous_texts.append(previous_text)

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
