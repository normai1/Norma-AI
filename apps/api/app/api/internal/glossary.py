"""
Internal, service-to-service routes for apps/voice (item 20b). Distinct
from the public /api/v1 surface - a different trust boundary
(RequireInternalSecret, not a user session) deserves a visually distinct
prefix so nobody mistakes it for a publicly reachable endpoint.
"""

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.api.internal_deps import RequireInternalSecret
from app.repositories import assistant as assistant_repo
from app.repositories import glossary_entry as glossary_entry_repo

router = APIRouter(tags=["internal"])

_ASSISTANT_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Assistant not found",
)


@router.get("/internal/v1/assistants/{assistant_id}/glossary")
async def get_assistant_glossary(
    assistant_id: uuid.UUID,
    db: DbSession,
    _: RequireInternalSecret,
) -> dict[str, list[str]]:
    """
    Plain glossary term strings for STT keyword biasing - not the full
    GlossaryEntry shape. phonetic_spelling (TTS pronunciation) and
    stt_boost_weight (not yet wired into any provider) are deliberately
    left out; nothing downstream of this endpoint uses them today.
    """

    assistant = await assistant_repo.get_by_id(db, assistant_id)

    if assistant is None:
        raise _ASSISTANT_NOT_FOUND

    entries = await glossary_entry_repo.list_for_assistant(db, assistant_id)

    return {"terms": [entry.term for entry in entries]}
