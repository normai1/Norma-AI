import uuid

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import DbSession
from app.api.org_deps import CanManageKnowledge
from app.api.workspace_deps import CurrentWorkspace
from app.core.exceptions import FaqEntryNotFound, WorkspaceNotFound
from app.schemas.faq_entry import FaqEntryCreate, FaqEntryResponse, FaqEntryUpdate
from app.services import faq_entry as faq_entry_service

router = APIRouter(tags=["faq-entries"])

_FAQ_ENTRY_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="FAQ entry not found",
)

_WORKSPACE_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Workspace not found",
)

_PREFIX = (
    "/organizations/{organization_id}/workspaces/{workspace_id}"
    "/knowledge-sources/{knowledge_source_id}/faq-entries"
)


@router.post(
    _PREFIX,
    response_model=FaqEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_faq_entry(
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
    payload: FaqEntryCreate,
    membership: CanManageKnowledge,
    db: DbSession,
) -> FaqEntryResponse:
    """
    Add a FAQ entry to a manual-FAQ knowledge source. Owners and admins only.
    """

    try:
        faq_entry = await faq_entry_service.create_faq_entry(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            knowledge_source_id=knowledge_source_id,
            question=payload.question,
            answer=payload.answer,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except FaqEntryNotFound as exc:
        raise _FAQ_ENTRY_NOT_FOUND from exc

    await db.commit()

    return FaqEntryResponse.model_validate(faq_entry)


@router.get(_PREFIX, response_model=list[FaqEntryResponse])
async def list_faq_entries(
    knowledge_source_id: uuid.UUID,
    workspace: CurrentWorkspace,
    db: DbSession,
) -> list[FaqEntryResponse]:
    """
    List a manual-FAQ knowledge source's entries. Any workspace member may
    see them.
    """

    try:
        faq_entries = await faq_entry_service.list_faq_entries(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            knowledge_source_id=knowledge_source_id,
        )
    except FaqEntryNotFound as exc:
        raise _FAQ_ENTRY_NOT_FOUND from exc

    return [FaqEntryResponse.model_validate(entry) for entry in faq_entries]


@router.patch(
    f"{_PREFIX}/{{faq_entry_id}}",
    response_model=FaqEntryResponse,
)
async def update_faq_entry(
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
    faq_entry_id: uuid.UUID,
    payload: FaqEntryUpdate,
    membership: CanManageKnowledge,
    db: DbSession,
) -> FaqEntryResponse:
    """
    Apply a partial update to a FAQ entry. Owners and admins only.
    """

    fields = payload.model_dump(exclude_unset=True)

    try:
        faq_entry = await faq_entry_service.update_faq_entry(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            knowledge_source_id=knowledge_source_id,
            faq_entry_id=faq_entry_id,
            **fields,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except FaqEntryNotFound as exc:
        raise _FAQ_ENTRY_NOT_FOUND from exc

    await db.commit()

    return FaqEntryResponse.model_validate(faq_entry)


@router.delete(
    f"{_PREFIX}/{{faq_entry_id}}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_faq_entry(
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
    faq_entry_id: uuid.UUID,
    membership: CanManageKnowledge,
    db: DbSession,
) -> Response:
    """
    Permanently remove a FAQ entry. Owners and admins only.
    """

    try:
        await faq_entry_service.delete_faq_entry(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            knowledge_source_id=knowledge_source_id,
            faq_entry_id=faq_entry_id,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except FaqEntryNotFound as exc:
        raise _FAQ_ENTRY_NOT_FOUND from exc

    await db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
