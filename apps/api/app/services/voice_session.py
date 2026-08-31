import uuid

from norma_shared.voice_session_ticket import create_voice_session_ticket
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.services import assistant as assistant_service

# Long enough to cover issuance-to-connection latency, short enough that a
# leaked ticket is worthless within seconds - the ticket authorizes
# connecting, not the call itself (item 21a's spec).
TICKET_TTL_SECONDS = 60


async def issue_test_call_ticket(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
) -> tuple[str, int]:
    """
    Confirm the assistant exists in this workspace, then issue a
    voice-session ticket for it. Raises AssistantNotFound (from
    assistant_service.get_assistant) unchanged - the caller already knows
    how to turn that into a 404.
    """

    await assistant_service.get_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    ticket = create_voice_session_ticket(
        secret_key=settings.secret_key,
        algorithm=settings.jwt_algorithm,
        assistant_id=str(assistant_id),
        ttl_seconds=TICKET_TTL_SECONDS,
    )

    return ticket, TICKET_TTL_SECONDS
