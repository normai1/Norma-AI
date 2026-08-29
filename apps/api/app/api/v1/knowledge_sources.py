import uuid
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, UploadFile, status

from app.api.deps import DbSession, PageFetcherDep, StorageProviderDep
from app.api.org_deps import CanManageKnowledge
from app.api.workspace_deps import CurrentWorkspace
from app.core.exceptions import (
    FileTooLarge,
    KnowledgeSourceNotFound,
    UnsupportedFileType,
    WorkspaceNotFound,
)
from app.models.crawled_page import CrawledPage
from app.models.document import Document
from app.models.knowledge_source import KnowledgeSource
from app.schemas.knowledge_source import (
    CrawledPageResponse,
    DocumentResponse,
    KnowledgeSourceResponse,
    ManualFaqKnowledgeSourceCreate,
    WebsiteKnowledgeSourceCreate,
)
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
    crawled_pages: list[CrawledPage] | None = None,
) -> KnowledgeSourceResponse:
    return KnowledgeSourceResponse(
        id=knowledge_source.id,
        organization_id=knowledge_source.organization_id,
        workspace_id=knowledge_source.workspace_id,
        type=knowledge_source.type,
        status=knowledge_source.status,
        error_message=knowledge_source.error_message,
        owner_user_id=knowledge_source.owner_user_id,
        source_url=knowledge_source.source_url,
        name=knowledge_source.name,
        created_at=knowledge_source.created_at,
        document=DocumentResponse.model_validate(document) if document else None,
        crawled_pages=(
            [CrawledPageResponse.model_validate(page) for page in crawled_pages]
            if crawled_pages is not None
            else None
        ),
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


@router.post(
    f"{_PREFIX}/website",
    response_model=KnowledgeSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_website_knowledge_source(
    workspace_id: uuid.UUID,
    payload: WebsiteKnowledgeSourceCreate,
    membership: CanManageKnowledge,
    db: DbSession,
    fetcher: PageFetcherDep,
) -> KnowledgeSourceResponse:
    """
    Crawl a domain as a new knowledge source. Owners and admins only. Runs
    synchronously - a deliberate, bounded (20 pages, depth 2) MVP shape;
    there is no background job queue yet.
    """

    try:
        (
            knowledge_source,
            crawled_pages,
        ) = await knowledge_source_service.create_website_knowledge_source(
            db,
            fetcher,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            owner_user_id=membership.user_id,
            url=str(payload.url),
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc

    await db.commit()

    return _to_response(knowledge_source, None, crawled_pages)


@router.post(
    f"{_PREFIX}/{{knowledge_source_id}}/recrawl",
    response_model=KnowledgeSourceResponse,
)
async def recrawl_knowledge_source(
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
    membership: CanManageKnowledge,
    db: DbSession,
    fetcher: PageFetcherDep,
) -> KnowledgeSourceResponse:
    """
    Re-crawl an existing website-type knowledge source. Owners and admins
    only. Unchanged pages are left alone; only pages whose content actually
    changed are rewritten.
    """

    try:
        (
            knowledge_source,
            crawled_pages,
        ) = await knowledge_source_service.recrawl_knowledge_source(
            db,
            fetcher,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            knowledge_source_id=knowledge_source_id,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except KnowledgeSourceNotFound as exc:
        raise _KNOWLEDGE_SOURCE_NOT_FOUND from exc

    await db.commit()

    return _to_response(knowledge_source, None, crawled_pages)


@router.post(
    f"{_PREFIX}/manual-faq",
    response_model=KnowledgeSourceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_manual_faq_knowledge_source(
    workspace_id: uuid.UUID,
    payload: ManualFaqKnowledgeSourceCreate,
    membership: CanManageKnowledge,
    db: DbSession,
) -> KnowledgeSourceResponse:
    """
    Create a new manual-FAQ knowledge source. Owners and admins only.
    """

    try:
        knowledge_source = (
            await knowledge_source_service.create_manual_faq_knowledge_source(
                db,
                organization_id=membership.organization_id,
                workspace_id=workspace_id,
                owner_user_id=membership.user_id,
                name=payload.name,
            )
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc

    await db.commit()

    return _to_response(knowledge_source, None)


@router.get(_PREFIX, response_model=list[KnowledgeSourceResponse])
async def list_knowledge_sources(
    workspace: CurrentWorkspace,
    db: DbSession,
) -> list[KnowledgeSourceResponse]:
    """
    List knowledge sources in a workspace. Any workspace member may see them.
    """

    triples = await knowledge_source_service.list_knowledge_sources(
        db,
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
    )

    return [
        _to_response(source, document, crawled_pages)
        for source, document, crawled_pages in triples
    ]


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
            crawled_pages,
        ) = await knowledge_source_service.get_knowledge_source(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            knowledge_source_id=knowledge_source_id,
        )
    except KnowledgeSourceNotFound as exc:
        raise _KNOWLEDGE_SOURCE_NOT_FOUND from exc

    return _to_response(knowledge_source, document, crawled_pages)
