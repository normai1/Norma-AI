import logging
import uuid
from typing import Annotated

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Response,
    UploadFile,
    status,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.api.deps import (
    DbSession,
    EmbeddingProviderDep,
    FaqGenerationLlmProviderDep,
    PageFetcherDep,
    StorageProviderDep,
)
from app.api.org_deps import CanManageKnowledge
from app.api.workspace_deps import CurrentWorkspace
from app.core.database import get_session_factory
from app.core.exceptions import (
    AssistantNotFound,
    FileTooLarge,
    InvalidKnowledgeSourceType,
    KnowledgeSourceNotFound,
    UnsupportedFileType,
    WorkspaceNotFound,
)
from app.models.crawled_page import CrawledPage
from app.models.document import Document
from app.models.knowledge_source import KnowledgeSource
from app.repositories import chunk as chunk_repo
from app.repositories import knowledge_source as knowledge_source_repo
from app.schemas.chunk import ChunkResponse
from app.schemas.knowledge_source import (
    CrawledPageResponse,
    DocumentResponse,
    KnowledgeSourceResponse,
    ManualFaqKnowledgeSourceCreate,
    WebsiteKnowledgeSourceCreate,
)
from app.services import knowledge_source as knowledge_source_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["knowledge-sources"])

_KNOWLEDGE_SOURCE_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Knowledge source not found",
)

_WORKSPACE_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Workspace not found",
)

_ASSISTANT_NOT_FOUND = HTTPException(
    status_code=status.HTTP_404_NOT_FOUND,
    detail="Assistant not found",
)

_UNSUPPORTED_FILE_TYPE = HTTPException(
    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    detail="Unsupported file type. Accepted: .pdf, .docx, .md, .txt",
)

_FILE_TOO_LARGE = HTTPException(
    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    detail="File is too large",
)

_INVALID_SOURCE_TYPE = HTTPException(
    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
    detail="This operation only applies to file-type knowledge sources",
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
        assistant_id=knowledge_source.assistant_id,
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
    embedding_provider: EmbeddingProviderDep,
    faq_llm_provider: FaqGenerationLlmProviderDep,
    background_tasks: BackgroundTasks,
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
    file: Annotated[UploadFile, File()],
    assistant_id: Annotated[uuid.UUID, Form()],
) -> KnowledgeSourceResponse:
    """
    Upload a file as a new knowledge source, assigned to one assistant.
    Owners and admins only.

    Parsing, chunking and embedding still happen here, so the response
    already reports whether the document itself was usable. FAQ generation
    is scheduled instead: it queues itself behind the provider's
    tokens-per-minute budget and a long document takes minutes to work
    through, which is a background job's business and not an upload's.
    """

    content = await file.read()

    try:
        (
            knowledge_source,
            document,
        ) = await knowledge_source_service.upload_knowledge_source(
            db,
            storage,
            embedding_provider,
            None,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            assistant_id=assistant_id,
            owner_user_id=membership.user_id,
            filename=file.filename or "",
            content=content,
            faq_generation_scheduled=True,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc
    except UnsupportedFileType as exc:
        raise _UNSUPPORTED_FILE_TYPE from exc
    except FileTooLarge as exc:
        raise _FILE_TOO_LARGE from exc

    await db.commit()

    if document.processing_status == knowledge_source_service.COMPLETED_STATUS:
        background_tasks.add_task(
            _generate_faqs_in_background,
            session_factory=session_factory,
            storage=storage,
            llm_provider=faq_llm_provider,
            embedding_provider=embedding_provider,
            knowledge_source_id=knowledge_source.id,
        )

    return _to_response(knowledge_source, document)


async def _generate_faqs_in_background(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    storage: StorageProviderDep,
    llm_provider: FaqGenerationLlmProviderDep,
    embedding_provider: EmbeddingProviderDep,
    knowledge_source_id: uuid.UUID,
) -> None:
    """
    Write a just-uploaded file source's FAQ entries after its request has
    returned, on its own session - the request's is closed by the time this
    runs, exactly as for the website crawl above.

    Failure is logged and dropped. There is no caller left to receive it, and
    the source itself already completed: the operator sees a document that
    worked and a shorter FAQ list than they hoped for, not a broken upload.
    """

    async with session_factory() as session:
        try:
            await knowledge_source_service.generate_faqs_for_file_source(
                session,
                storage,
                llm_provider,
                embedding_provider,
                knowledge_source_id=knowledge_source_id,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception(
                "FAQ generation failed for knowledge source %s", knowledge_source_id
            )

            # The source is left mid-flight otherwise: it was moved to
            # "processing" before generation started, and nothing else
            # will ever move it off. An operator would watch it spin for
            # good.
            await _mark_faq_generation_failed(session, knowledge_source_id)

            # The source is left mid-flight otherwise: it was moved to
            # "processing" before generation started, and nothing else will
            # ever move it off. An operator would watch it spin for good.
            await _mark_faq_generation_failed(session, knowledge_source_id)


async def _mark_faq_generation_failed(
    session: AsyncSession, knowledge_source_id: uuid.UUID
) -> None:
    """
    Record that FAQ generation fell over, on its own transaction - the one
    it happened in has already been rolled back.
    """

    try:
        source = await knowledge_source_repo.get_by_id(session, knowledge_source_id)

        if source is None:
            return

        source.status = knowledge_source_service.FAILED_STATUS
        source.error_message = (
            "Something went wrong while writing this document's FAQs. Retry "
            "to try again."
        )
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception(
            "could not record the FAQ generation failure for knowledge "
            "source %s",
            knowledge_source_id,
        )


async def _mark_faq_generation_failed(
    session: AsyncSession, knowledge_source_id: uuid.UUID
) -> None:
    """
    Record that FAQ generation fell over, on its own transaction - the one it
    happened in has already been rolled back.
    """

    try:
        source = await knowledge_source_repo.get_by_id(session, knowledge_source_id)

        if source is None:
            return

        source.status = knowledge_source_service.FAILED_STATUS
        source.error_message = (
            "Something went wrong while writing this document's FAQs. Retry "
            "to try again."
        )
        await session.commit()
    except Exception:
        await session.rollback()
        logger.exception(
            "could not record the FAQ generation failure for knowledge source %s",
            knowledge_source_id,
        )


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
    background_tasks: BackgroundTasks,
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
    fetcher: PageFetcherDep,
    embedding_provider: EmbeddingProviderDep,
    faq_llm_provider: FaqGenerationLlmProviderDep,
) -> KnowledgeSourceResponse:
    """
    Add a website as a knowledge source. Owners and admins only.

    Returns as soon as the source is registered, with the crawl running
    afterwards in the background and the source's own status reporting
    progress - the same pending/processing/completed/failed lifecycle a
    file upload already uses. Crawling a whole site takes minutes at the
    configured page budget, far longer than a request should be held open,
    and the caller does not need the pages to know the source was accepted.
    """

    try:
        knowledge_source = (
            await knowledge_source_service.register_website_knowledge_source(
                db,
                organization_id=membership.organization_id,
                workspace_id=workspace_id,
                assistant_id=payload.assistant_id,
                owner_user_id=membership.user_id,
                url=str(payload.url),
            )
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc

    await db.commit()

    background_tasks.add_task(
        _crawl_website_source_in_background,
        session_factory=session_factory,
        knowledge_source_id=knowledge_source.id,
        fetcher=fetcher,
        embedding_provider=embedding_provider,
        faq_llm_provider=faq_llm_provider,
    )

    return _to_response(knowledge_source, None, [])


async def _crawl_website_source_in_background(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    knowledge_source_id: uuid.UUID,
    fetcher: PageFetcherDep,
    embedding_provider: EmbeddingProviderDep,
    faq_llm_provider: FaqGenerationLlmProviderDep,
) -> None:
    """
    Crawl a registered website source after its request has returned.

    Opens its own session: the request's session is closed by the time this
    runs. Any failure is recorded on the source itself (the crawl path marks
    it failed with the error) rather than raised, since there is no caller
    left to receive it - the operator sees it on the source in the UI.
    """

    async with session_factory() as session:
        knowledge_source = await knowledge_source_repo.get_by_id(
            session, knowledge_source_id
        )

        if knowledge_source is None:
            return

        # Committed before the crawl starts, on its own, so the very next poll
        # sees "processing" rather than a "pending" row that looks identical to
        # one nothing is working on.
        knowledge_source.status = knowledge_source_service.PROCESSING_STATUS
        knowledge_source.error_message = None
        await session.commit()

        try:
            await knowledge_source_service.crawl_website_knowledge_source(
                session,
                fetcher,
                embedding_provider,
                faq_llm_provider,
                knowledge_source=knowledge_source,
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.exception(
                "website crawl failed for knowledge source %s", knowledge_source_id
            )


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
    embedding_provider: EmbeddingProviderDep,
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
            embedding_provider,
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
                assistant_id=payload.assistant_id,
                owner_user_id=membership.user_id,
                name=payload.name,
            )
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except AssistantNotFound as exc:
        raise _ASSISTANT_NOT_FOUND from exc

    await db.commit()

    return _to_response(knowledge_source, None)


@router.post(
    f"{_PREFIX}/{{knowledge_source_id}}/process",
    response_model=KnowledgeSourceResponse,
)
async def process_knowledge_source(
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
    membership: CanManageKnowledge,
    db: DbSession,
    storage: StorageProviderDep,
    embedding_provider: EmbeddingProviderDep,
    faq_llm_provider: FaqGenerationLlmProviderDep,
    background_tasks: BackgroundTasks,
    session_factory: Annotated[
        async_sessionmaker[AsyncSession], Depends(get_session_factory)
    ],
) -> KnowledgeSourceResponse:
    """
    Retry parsing+chunking+embedding a file-type source's already-stored
    document. Owners and admins only.

    Also the way back to a source whose FAQ generation was cut short - by
    the provider's daily token allowance running out mid-document, say,
    which leaves a source that is not finished and, before this, no way to
    finish it.

    Generation is scheduled only for a source that has not already finished
    it. Reprocessing one that did re-reads and re-embeds the document, which
    is what was asked for, without spending a second document's worth of the
    provider's daily allowance rewriting questions that already exist.
    """

    existing = await knowledge_source_repo.get_by_id(db, knowledge_source_id)
    needs_faqs = (
        existing is not None
        and existing.status != knowledge_source_service.COMPLETED_STATUS
    )

    try:
        (
            knowledge_source,
            document,
        ) = await knowledge_source_service.process_knowledge_source(
            db,
            storage,
            embedding_provider,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            knowledge_source_id=knowledge_source_id,
            faq_generation_scheduled=needs_faqs,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except KnowledgeSourceNotFound as exc:
        raise _KNOWLEDGE_SOURCE_NOT_FOUND from exc
    except InvalidKnowledgeSourceType as exc:
        raise _INVALID_SOURCE_TYPE from exc

    await db.commit()

    if (
        needs_faqs
        and document.processing_status == knowledge_source_service.COMPLETED_STATUS
    ):
        background_tasks.add_task(
            _generate_faqs_in_background,
            session_factory=session_factory,
            storage=storage,
            llm_provider=faq_llm_provider,
            embedding_provider=embedding_provider,
            knowledge_source_id=knowledge_source.id,
        )

    return _to_response(knowledge_source, document)


@router.get(
    f"{_PREFIX}/{{knowledge_source_id}}/chunks",
    response_model=list[ChunkResponse],
)
async def list_chunks(
    knowledge_source_id: uuid.UUID,
    workspace: CurrentWorkspace,
    db: DbSession,
) -> list[ChunkResponse]:
    """
    List a knowledge source's chunks, in order. Any workspace member may
    see them, but only for a source in their own workspace -
    resolve_knowledge_source confirms that before any chunk is read.
    """

    try:
        knowledge_source = await knowledge_source_service.resolve_knowledge_source(
            db,
            organization_id=workspace.organization_id,
            workspace_id=workspace.id,
            knowledge_source_id=knowledge_source_id,
        )
    except KnowledgeSourceNotFound as exc:
        raise _KNOWLEDGE_SOURCE_NOT_FOUND from exc

    chunks = await chunk_repo.list_for_source(db, knowledge_source.id)

    return [ChunkResponse.model_validate(chunk) for chunk in chunks]


@router.get(_PREFIX, response_model=list[KnowledgeSourceResponse])
async def list_knowledge_sources(
    workspace: CurrentWorkspace,
    db: DbSession,
    assistant_id: uuid.UUID | None = None,
) -> list[KnowledgeSourceResponse]:
    """
    List knowledge sources in a workspace. Any workspace member may see them.

    assistant_id narrows the list to that assistant's own knowledge base -
    each assistant has a separate one, and retrieval has only ever searched
    the assistant's own chunks (item 23d). Filtering here rather than in the
    browser keeps a sibling assistant's documents off the wire entirely.
    Omitting it returns every source in the workspace, unchanged.
    """

    triples = await knowledge_source_service.list_knowledge_sources(
        db,
        organization_id=workspace.organization_id,
        workspace_id=workspace.id,
        assistant_id=assistant_id,
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


@router.delete(
    f"{_PREFIX}/{{knowledge_source_id}}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_knowledge_source(
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
    membership: CanManageKnowledge,
    db: DbSession,
    storage: StorageProviderDep,
) -> Response:
    """
    Permanently delete a knowledge source. Owners and admins only.
    Irreversible - cascades to its document, chunks, and crawled pages, and
    removes the underlying S3 object for a file-type source.
    """

    try:
        await knowledge_source_service.delete_knowledge_source(
            db,
            storage,
            organization_id=membership.organization_id,
            workspace_id=workspace_id,
            knowledge_source_id=knowledge_source_id,
        )
    except WorkspaceNotFound as exc:
        raise _WORKSPACE_NOT_FOUND from exc
    except KnowledgeSourceNotFound as exc:
        raise _KNOWLEDGE_SOURCE_NOT_FOUND from exc

    await db.commit()

    return Response(status_code=status.HTTP_204_NO_CONTENT)
