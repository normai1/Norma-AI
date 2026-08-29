import uuid

from fastapi import APIRouter, HTTPException, Response, status

from app.api.deps import DbSession
from app.api.org_deps import CanManageAssistants
from app.api.workspace_deps import CurrentWorkspace
from app.core.exceptions import (
    AssistantNotFound,
    GlossaryEntryAlreadyExists,
    GlossaryEntryNotFound,
    WorkspaceNotFound,
)
from app.schemas.glossary_entry import (
    GlossaryEntryCreate,
    GlossaryEntryResponse,
    GlossaryEntryUpdate,
)
from app.services import glossary_entry as glossary_entry_service

router = APIRouter(tags=["glossary-entries"])

_ASSISTANT_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Assistant not found",
)

_GLOSSARY_ENTRY_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Glossary entry not found",
)

_GLOSSARY_ENTRY_ALREADY_EXISTS = HTTPException(
    status_code=status.HTTP_409_CONFLICT,
    detail="This assistant already has a glossary entry for this term",
)

_WORKSPACE_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Workspace not found",
)

_PREFIX = (
    "/organizations/{organization_id}/workspaces/{workspace_id}"
    "/assistants/{assistant_id}/glossary"
)


@router.post(
    _PREFIX,
    response_model=GlossaryEntryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_glossary_entry(
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    payload: GlossaryEntryCreate,
    membership: CanManageAssistants,
    db: DbSession,
) -> GlossaryEntryResponse:
    """
    Add a glossary entry to an assistant. Owners and admins only.
    """

    try:
        glossary_entry = await glossary_entry_service.create_glossary_entry(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            assistant_id=assistant_id,
            term=payload.term,
            meaning=payload.meaning,
            phonetic_spelling=payload.phonetic_spelling,
            stt_boost_weight=payload.stt_boost_weight,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc
    except GlossaryEntryAlreadyExists as exc:
        raise _GLOSSARY_ENTRY_ALREADY_EXISTS from exc

    await db.commit()

    return GlossaryEntryResponse.model_validate(glossary_entry)


@router.get(_PREFIX, response_model=list[GlossaryEntryResponse])
async def list_glossary_entries(
    assistant_id: uuid.UUID,
    workspace: CurrentWorkspace,
    db: DbSession,
) -> list[GlossaryEntryResponse]:
    """
    List an assistant's glossary entries. Any workspace member may see them.
    """

    try:
        glossary_entries = await glossary_entry_service.list_glossary_entries(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            assistant_id=assistant_id,
        )
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc

    return [
        GlossaryEntryResponse.model_validate(glossary_entry)
        for glossary_entry in glossary_entries
    ]


@router.patch(
    f"{_PREFIX}/{{glossary_entry_id}}",
    response_model=GlossaryEntryResponse,
)
async def update_glossary_entry(
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    glossary_entry_id: uuid.UUID,
    payload: GlossaryEntryUpdate,
    membership: CanManageAssistants,
    db: DbSession,
) -> GlossaryEntryResponse:
    """
    Apply a partial update to a glossary entry. Owners and admins only.
    """

    fields = payload.model_dump(exclude_unset=True)

    try:
        glossary_entry = await glossary_entry_service.update_glossary_entry(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            assistant_id=assistant_id,
            glossary_entry_id=glossary_entry_id,
            **fields,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc
    except GlossaryEntryNotFound as exc:
        raise _GLOSSARY_ENTRY_NOT_FOUND from exc
    except GlossaryEntryAlreadyExists as exc:
        raise _GLOSSARY_ENTRY_ALREADY_EXISTS from exc

    await db.commit()

    return GlossaryEntryResponse.model_validate(glossary_entry)


@router.delete(
    f"{_PREFIX}/{{glossary_entry_id}}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_glossary_entry(
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    glossary_entry_id: uuid.UUID,
    membership: CanManageAssistants,
    db: DbSession,
) -> Response:
    """
    Permanently remove a glossary entry. Owners and admins only.
    """

    try:
        await glossary_entry_service.delete_glossary_entry(
            db,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            assistant_id=assistant_id,
            glossary_entry_id=glossary_entry_id,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc
    except GlossaryEntryNotFound as exc:
        raise _GLOSSARY_ENTRY_NOT_FOUND from exc

    await db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
