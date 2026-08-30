"""
Speech provider contracts: the streaming interfaces every speech-to-text and
text-to-speech implementation in this codebase is built against. Locked by
feature 9a - see blueprint/history/features/09a-speech-provider-contracts-and-mocks.md
for why these shapes are the way they are.
"""

from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
from typing import Protocol

# Canonical internal audio format. Every provider speaks this format at the
# interface boundary; carrier audio (commonly 8 kHz mu-law) is transcoded at
# the telephony edge (item 23), never inside a speech provider.
AUDIO_SAMPLE_RATE_HZ = 16_000
AUDIO_SAMPLE_WIDTH_BYTES = 2  # 16-bit signed
AUDIO_CHANNELS = 1  # mono


@dataclass(frozen=True)
class TranscriptEvent:
    """
    One STT output event. Partial events are not optional decoration - turn
    detection (item 20c) and barge-in (item 20e) both need transcripts before
    the caller finishes speaking.
    """

    text: str
    is_final: bool
    confidence: float | None = None


class SpeechProviderError(Exception):
    """
    Base class for a speech provider's own failures, distinct from a bug in
    the calling code. Callers catch this to fall back to forwarding or
    message-taking rather than crashing the call - the single-vendor decision
    in project-overview.md's Speech section means there is no second speech
    provider to fail over to.
    """


class SpeechProviderTimeout(SpeechProviderError):
    """
    The provider did not respond within the caller's bound.
    """


class SpeechProviderUnavailable(SpeechProviderError):
    """
    The provider rejected the request, or the connection could not be
    established - auth failure, outage, or rate limit.
    """


class SpeechToTextProvider(Protocol):
    """
    Streaming speech-to-text. An implementation consumes an audio stream and
    yields transcript events as they become available.
    """

    def stream(
        self,
        audio: AsyncIterator[bytes],
        *,
        language: str,
        keywords: Sequence[str] = (),
    ) -> AsyncIterator[TranscriptEvent]:
        """
        Transcribe streaming audio, yielding partial and final events in
        order. keywords are glossary terms (item 13) biasing recognition
        toward domain vocabulary. Closing the returned iterator early must
        stop transcription promptly.
        """
        ...


@dataclass(frozen=True)
class Voice:
    """
    One selectable voice in a provider's catalogue. Feeds item 10's voice
    catalogue and preview UI.
    """

    id: str
    name: str
    language: str
    gender: str | None = None
    preview_url: str | None = None


class TextToSpeechProvider(Protocol):
    """
    Streaming text-to-speech. An implementation synthesizes audio chunks as
    they become available, so playback can start before synthesis finishes.
    """

    def synthesize(
        self,
        text: str,
        *,
        voice_id: str,
        speed: float = 1.0,
    ) -> AsyncIterator[bytes]:
        """
        Synthesize speech audio for text, streaming chunks in the canonical
        format as they are produced. Closing the returned iterator early -
        the barge-in mechanism - must stop synthesis promptly, within the
        200ms barge-in budget.
        """
        ...

    async def list_voices(self) -> Sequence[Voice]:
        """
        The provider's available voices.
        """
        ...
