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
from app.repositories import assistant_version as assistant_version_repo
from app.schemas.assistant_version import AssistantVersionCreate

router = APIRouter(tags=["internal"])

_ASSISTANT_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Assistant not found",
)

# The schema's own default (app/schemas/assistant_version.py) - an assistant
# that has never been published has no current_version_id yet, but a test
# call in the browser (item 21) must still be possible before formal
# publishing, so this falls back to the same default a real version would
# have started from rather than inventing a different number.
_DEFAULT_SENSITIVITY = AssistantVersionCreate.model_fields["turn_sensitivity"].default


@router.get("/internal/v1/assistants/{assistant_id}/turn-detection-config")
async def get_turn_detection_config(
    assistant_id: uuid.UUID,
    db: DbSession,
    _: RequireInternalSecret,
) -> dict[str, float]:
    assistant = await assistant_repo.get_by_id(db, assistant_id)

    if assistant is None:
        raise _ASSISTANT_NOT_FOUND

    if assistant.current_version_id is None:
        return {"sensitivity": _DEFAULT_SENSITIVITY}

    version = await assistant_version_repo.get_by_id(db, assistant.current_version_id)

    return {"sensitivity": version.turn_sensitivity}
