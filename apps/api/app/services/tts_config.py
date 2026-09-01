"""
Resolves the two pieces of assistant configuration item 20e's TTS stage
needs at session setup: voice_id and speech_rate, read directly off the one
mutable Assistant row. Mirrors app/services/llm_config.py's exact shape - a
separate endpoint from that one, not folded in, for the same "different
concern, no benefit to merging" reasoning item 20d already applied to
llm-config vs. retrieve.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AssistantNotFound
from app.repositories import assistant as assistant_repo

# voice_id has no DB default and stays nullable - a freshly created assistant
# has none chosen yet, so this fallback is only ever reached by a stray
# test-call attempt against a genuinely unconfigured assistant, never a real
# call.
DEFAULT_VOICE_ID = "default"


@dataclass(frozen=True)
class TTSConfig:
    voice_id: str
    speech_rate: float


async def resolve_tts_config(db: AsyncSession, assistant_id: uuid.UUID) -> TTSConfig:
    assistant = await assistant_repo.get_by_id(db, assistant_id)

    if assistant is None:
        raise AssistantNotFound

    return TTSConfig(
        voice_id=assistant.voice_id or DEFAULT_VOICE_ID,
        speech_rate=assistant.speech_rate,
    )
