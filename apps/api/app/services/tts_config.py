"""
Resolves the two pieces of assistant configuration item 20e's TTS stage
needs at session setup: voice_id and speech_rate. Mirrors
app/services/llm_config.py's exact shape - a separate endpoint from that
one, not folded in, for the same "different concern, no benefit to merging"
reasoning item 20d already applied to llm-config vs. retrieve.
"""

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import AssistantNotFound
from app.repositories import assistant as assistant_repo
from app.repositories import assistant_version as assistant_version_repo

# voice_id has no schema default - it is a required field an assistant
# cannot be created without (app/schemas/assistant_version.py), so this
# fallback is only ever reached by a stray test-call attempt against a
# genuinely unpublished assistant, never a real call.
DEFAULT_VOICE_ID = "default"

# AssistantVersionCreate's own schema default.
DEFAULT_SPEECH_RATE = 1.0


@dataclass(frozen=True)
class TTSConfig:
    voice_id: str
    speech_rate: float


async def resolve_tts_config(db: AsyncSession, assistant_id: uuid.UUID) -> TTSConfig:
    assistant = await assistant_repo.get_by_id(db, assistant_id)

    if assistant is None:
        raise AssistantNotFound

    if assistant.current_version_id is None:
        return TTSConfig(voice_id=DEFAULT_VOICE_ID, speech_rate=DEFAULT_SPEECH_RATE)

    version = await assistant_version_repo.get_by_id(db, assistant.current_version_id)

    return TTSConfig(voice_id=version.voice_id, speech_rate=version.speech_rate)
