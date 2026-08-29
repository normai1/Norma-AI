import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.api.deps import DbSession, StorageProviderDep
from app.api.org_deps import CanManageKnowledge
from app.api.workspace_deps import CurrentWorkspace
from app.core.exceptions import (
    FileTooLarge,
    KnowledgeSourceNotFound,
    UnsupportedFileType,
    WorkspaceNotFound,
)
from app.models.document import Document
from app.models.knowledge_source import KnowledgeSource
from app.schemas.knowledge_source import DocumentResponse, KnowledgeSourceResponse
from app.services import knowledge_source as knowledge_source_service

router = APIRouter(tags=["knowledge-sources"])

_KNOWLEDGE_SOURCE_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Knowledge source not found",
)

_WORKSPACE_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Workspace not found",
)

_UNSUPPORTED_FILE_TYPE = HTTPException(
    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    detail="Unsupported file type. Accepted: .pdf, .docx, .md, .txt",
)

_FILE_TOO_LARGE = HTTPException(
    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    detail="File is too large",
)

_PREFIX = "/organizations/{organization_id}/workspaces/{workspace_id}/knowledge-sources"


def _to_response(
    knowledge_source: KnowledgeSource,
    document: Document | None,
) -> KnowledgeSourceResponse:
    return KnowledgeSourceResponse(
        id=knowledge_source.id,
        organization_id=knowledge_source.organization_id,
        workspace_id=knowledge_source.workspace_id,
        type=knowledge_source.type,
        status=knowledge_source.status,
        error_message=knowledge_source.error_message,
        owner_user_id=knowledge_source.owner_user_id,
        created_at=knowledge_source.created_at,
        document=DocumentResponse.model_validate(document) if document else None,
    )


@router.post(
    _PREFIX,
    response_model=KnowledgeSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_knowledge_source(
    workspace_id: uuid.UUID,
    membership: CanManageKnowledge,
    db: DbSession,
    storage: StorageProviderDep,
    file: Annotated[UploadFile, File()],
) -> KnowledgeSourceResponse:
    """
    Upload a file as a new knowledge source. Owners and admins only.
    """

    content = await file.read()

    try:
        (
            knowledge_source,
            document,
        ) = await knowledge_source_service.upload_knowledge_source(
            db,
            storage,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            owner_user_id=membership.user_id,
            filename=file.filename or "",
            content=content,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except UnsupportedFileType as exc:
        raise _UNSUPPORTED_FILE_TYPE from exc
    except FileTooLarge as exc:
        raise _FILE_TOO_LARGE from exc

    await db.commit()

    return _to_response(knowledge_source, document)


@router.get(_PREFIX, response_model=list[KnowledgeSourceResponse])
async def list_knowledge_sources(
    workspace: CurrentWorkspace,
    db: DbSession,
) -> list[KnowledgeSourceResponse]:
    """
    List knowledge sources in a workspace. Any workspace member may see them.
    """

    pairs = await knowledge_source_service.list_knowledge_sources(
        db,
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
    )

    return [_to_response(source, document) for source, document in pairs]


@router.get(
    f"{_PREFIX}/{{knowledge_source_id}}", response_model=KnowledgeSourceResponse
)
async def get_knowledge_source(
    knowledge_source_id: uuid.UUID,
    workspace: CurrentWorkspace,
    db: DbSession,
) -> KnowledgeSourceResponse:
    """
    Fetch one knowledge source. Any workspace member may see it.
    """

    try:
        (
            knowledge_source,
            document,
        ) = await knowledge_source_service.get_knowledge_source(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            knowledge_source_id=knowledge_source_id,
        )
    except KnowledgeSourceNotFound as exc:
        raise _KNOWLEDGE_SOURCE_NOT_FOUND from exc

    return _to_response(knowledge_source, document)
