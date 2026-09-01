"""
Internal, service-to-service route exposing an assistant's turn-detection
sensitivity (item 20c) - a separate endpoint from app/api/internal/glossary.py
rather than folding this into that response, since the two are scoped to
different pipeline stages and this value is fetched once per session setup,
not per turn, so a second small round trip costs nothing latency-sensitive.
"""

import uuid

from fastapi import APIRouter, HTTPException, status

from app.api.deps import DbSession
from app.api.internal_deps import RequireInternalSecret
from app.repositories import assistant as assistant_repo

router = APIRouter(tags=["internal"])

_ASSISTANT_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Assistant not found",
)


@router.get("/internal/v1/assistants/{assistant_id}/turn-detection-config")
async def get_turn_detection_config(
    assistant_id: uuid.UUID,
    db: DbSession,
    _: RequireInternalSecret,
) -> dict[str, float]:
    assistant = await assistant_repo.get_by_id(db, assistant_id)

    if assistant is None:
        raise _ASSISTANT_NOT_FOUND

    return {"sensitivity": assistant.turn_sensitivity}
