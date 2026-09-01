import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    FileTooLarge,
    InvalidKnowledgeSourceType,
    KnowledgeSourceNotFound,
    UnsupportedFileType,
    WorkspaceNotFound,
)
from app.models.crawled_page import CrawledPage
from app.models.document import Document
from app.models.knowledge_source import KnowledgeSource
from app.providers.embedding import EmbeddingProvider, EmbeddingProviderError
from app.providers.storage import StorageObjectNotFound, StorageProvider
from app.providers.web_crawler import PageFetcher, PageFetchError
from app.repositories import chunk as chunk_repo
from app.repositories import crawled_page as crawled_page_repo
from app.repositories import knowledge_source as knowledge_source_repo
from app.repositories import workspace as workspace_repo
from app.repositories.chunk import ChunkWrite
from app.services import assistant as assistant_service
from app.services.chunker import ChunkSpan, chunk_text
from app.services.document_parser import DocumentParseError, parse_document
from app.services.web_crawler import crawl_website

FAILED_STATUS = "failed"
COMPLETED_STATUS = "completed"

MAX_UPLOAD_SIZE_BYTES = 20 * 1024 * 1024

# Extension is the authoritative check - client-declared MIME types are
# inconsistent across browsers, especially for .md.
_ALLOWED_EXTENSIONS: dict[str, str] = {
    ".pdf": "application/pdf",
    ".docx": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ),
    ".md": "text/markdown",
    ".txt": "text/plain",
}


def _extension_of(filename: str) -> str:
    dot_index = filename.rfind(".")

    return filename[dot_index:].lower() if dot_index != -1 else ""


async def _resolve_workspace_id(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> uuid.UUID:
    """
    Confirm the workspace exists in the caller's organization before any
    knowledge source operation touches it.
    """

    workspace = await workspace_repo.get_by_id(db, workspace_id)

    if workspace is None or workspace.organization_id != organization_id:
        raise WorkspaceNotFound

    return workspace.id


async def resolve_knowledge_source(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
) -> KnowledgeSource:
    """
    Look up a knowledge source, refusing one outside the caller's workspace.
    """

    await _resolve_workspace_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    knowledge_source = await knowledge_source_repo.get_by_id(db, knowledge_source_id)

    if knowledge_source is None or knowledge_source.workspace_id != workspace_id:
        raise KnowledgeSourceNotFound

    return knowledge_source


async def create_manual_faq_knowledge_source(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    name: str,
) -> KnowledgeSource:
    """
    Create a new manual-FAQ-type knowledge source. Status stays 'pending' -
    unlike a crawl, there is no operation here that can genuinely succeed or
    fail, so there is nothing to transition it to.
    """

    await _resolve_workspace_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )
    await assistant_service.get_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    knowledge_source = KnowledgeSource(
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
        type=knowledge_source_repo.MANUAL_FAQ_TYPE,
        owner_user_id=owner_user_id,
        name=name,
    )
    db.add(knowledge_source)
    await db.flush()

    return knowledge_source


async def _parse_and_chunk_document(
    db: AsyncSession,
    storage: StorageProvider,
    embedding_provider: EmbeddingProvider,
    knowledge_source: KnowledgeSource,
    document: Document,
) -> None:
    """
    (Re)parse a file-type source's stored document, (re)chunk it, and embed
    every chunk. A failure at any stage leaves any chunks from an earlier
    successful run untouched - replace_for_source is only called after
    parsing, chunking, AND embedding all succeed. Runs synchronously in the
    caller's request, the same deliberate, temporary tradeoff item 15's
    crawl already established; there is no background job queue yet.
    """

    extension = _extension_of(document.filename)

    try:
        content = await storage.download(document.storage_key)
        text = parse_document(content, extension)
    except (DocumentParseError, StorageObjectNotFound) as exc:
        message = (
            str(exc)
            if isinstance(exc, DocumentParseError)
            else "Stored file could not be found"
        )
        knowledge_source.status = FAILED_STATUS
        knowledge_source.error_message = message
        document.processing_status = FAILED_STATUS
        document.processing_error = message
        await db.flush()

        return

    spans = chunk_text(text)

    try:
        vectors = await embedding_provider.embed([span.text for span in spans])
    except EmbeddingProviderError as exc:
        message = str(exc)
        knowledge_source.status = FAILED_STATUS
        knowledge_source.error_message = message
        document.processing_status = FAILED_STATUS
        document.processing_error = message
        await db.flush()

        return

    await chunk_repo.replace_for_source(
        db,
        organization_id=knowledge_source.organization_id,
        workspace_id=knowledge_source.workspace_id,
        assistant_id=knowledge_source.assistant_id,
        knowledge_source_id=knowledge_source.id,
        chunks=[
            ChunkWrite(
                text=span.text,
                metadata={"char_start": span.char_start, "char_end": span.char_end},
                embedding=vector,
            )
            for span, vector in zip(spans, vectors, strict=True)
        ],
    )

    knowledge_source.status = COMPLETED_STATUS
    knowledge_source.error_message = None
    document.processing_status = COMPLETED_STATUS
    document.processing_error = None
    await db.flush()


async def process_knowledge_source(
    db: AsyncSession,
    storage: StorageProvider,
    embedding_provider: EmbeddingProvider,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
) -> tuple[KnowledgeSource, Document]:
    """
    Retry parsing+chunking+embedding a file-type source's already-stored
    document.
    """

    knowledge_source = await resolve_knowledge_source(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        knowledge_source_id=knowledge_source_id,
    )

    if knowledge_source.type != knowledge_source_repo.FILE_TYPE:
        raise InvalidKnowledgeSourceType

    document = await knowledge_source_repo.get_document_for_source(
        db, knowledge_source.id
    )

    if document is None:
        raise InvalidKnowledgeSourceType

    await _parse_and_chunk_document(
        db, storage, embedding_provider, knowledge_source, document
    )

    return knowledge_source, document


async def upload_knowledge_source(
    db: AsyncSession,
    storage: StorageProvider,
    embedding_provider: EmbeddingProvider,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    filename: str,
    content: bytes,
) -> tuple[KnowledgeSource, Document]:
    """
    Validate, store, and record a new file-type knowledge source. The upload
    happens before the DB insert; if the insert fails, the uploaded object is
    deleted rather than left orphaned with nothing pointing at it.
    """

    await _resolve_workspace_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )
    await assistant_service.get_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    extension = _extension_of(filename)

    if extension not in _ALLOWED_EXTENSIONS:
        raise UnsupportedFileType(extension)

    if len(content) > MAX_UPLOAD_SIZE_BYTES:
        raise FileTooLarge(
            f"{len(content)} bytes exceeds the {MAX_UPLOAD_SIZE_BYTES} cap"
        )

    content_type = _ALLOWED_EXTENSIONS[extension]
    key = (
        f"knowledge-sources/{organization_id}/{workspace_id}/{uuid.uuid4()}{extension}"
    )

    await storage.upload(key, content, content_type=content_type)

    try:
        knowledge_source, document = await knowledge_source_repo.create_with_document(
            db,
            organization_id=organization_id,
            workspace_id=workspace_id,
            assistant_id=assistant_id,
            owner_user_id=owner_user_id,
            filename=filename,
            content_type=content_type,
            storage_key=key,
        )
    except Exception:
        await storage.delete(key)
        raise

    await _parse_and_chunk_document(
        db, storage, embedding_provider, knowledge_source, document
    )

    return knowledge_source, document


async def list_knowledge_sources(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> list[tuple[KnowledgeSource, Document | None, list[CrawledPage] | None]]:
    """
    Every knowledge source in a workspace, each paired with its document
    (file-type) or crawled pages (website-type) - never both.
    """

    await _resolve_workspace_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    knowledge_sources = await knowledge_source_repo.list_for_workspace(db, workspace_id)

    file_source_ids = [
        source.id
        for source in knowledge_sources
        if source.type == knowledge_source_repo.FILE_TYPE
    ]
    documents = await knowledge_source_repo.list_documents_for_sources(
        db, file_source_ids
    )
    documents_by_source_id = {
        document.knowledge_source_id: document for document in documents
    }

    website_source_ids = [
        source.id
        for source in knowledge_sources
        if source.type == knowledge_source_repo.WEBSITE_TYPE
    ]
    pages = await crawled_page_repo.list_for_sources(db, website_source_ids)
    pages_by_source_id: dict[uuid.UUID, list[CrawledPage]] = {}
    for page in pages:
        pages_by_source_id.setdefault(page.knowledge_source_id, []).append(page)

    Triple = tuple[KnowledgeSource, Document | None, list[CrawledPage] | None]
    results: list[Triple] = []
    for source in knowledge_sources:
        if source.type == knowledge_source_repo.FILE_TYPE:
            results.append((source, documents_by_source_id.get(source.id), None))
        elif source.type == knowledge_source_repo.WEBSITE_TYPE:
            results.append((source, None, pages_by_source_id.get(source.id, [])))
        else:
            results.append((source, None, None))

    return results


async def delete_knowledge_source(
    db: AsyncSession,
    storage: StorageProvider,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
) -> None:
    """
    Permanently delete a knowledge source the caller may access. For a
    file-type source, the S3-backed document is deleted first - if that
    fails for a reason other than "already gone," the whole operation is
    aborted rather than deleting the DB row and silently orphaning the
    object (CLAUDE.md section 20: deletion must actually delete the
    object). website/manual_faq sources have nothing in object storage to
    clean up; Chunk/CrawledPage rows cascade via existing foreign keys.
    """

    knowledge_source = await resolve_knowledge_source(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        knowledge_source_id=knowledge_source_id,
    )

    if knowledge_source.type == knowledge_source_repo.FILE_TYPE:
        document = await knowledge_source_repo.get_document_for_source(
            db, knowledge_source.id
        )

        if document is not None:
            try:
                await storage.delete(document.storage_key)
            except StorageObjectNotFound:
                pass

    await knowledge_source_repo.delete(db, knowledge_source)


async def get_knowledge_source(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
) -> tuple[KnowledgeSource, Document | None, list[CrawledPage] | None]:
    """
    Fetch one knowledge source the caller may access, paired with its
    document (file-type) or crawled pages (website-type) - never both.
    """

    knowledge_source = await resolve_knowledge_source(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        knowledge_source_id=knowledge_source_id,
    )

    if knowledge_source.type == knowledge_source_repo.FILE_TYPE:
        document = await knowledge_source_repo.get_document_for_source(
            db, knowledge_source.id
        )

        return knowledge_source, document, None

    if knowledge_source.type == knowledge_source_repo.WEBSITE_TYPE:
        pages = await crawled_page_repo.list_for_source(db, knowledge_source.id)

        return knowledge_source, None, pages

    return knowledge_source, None, None


async def _crawl_and_reconcile(
    db: AsyncSession,
    fetcher: PageFetcher,
    embedding_provider: EmbeddingProvider,
    knowledge_source: KnowledgeSource,
    url: str,
) -> list[CrawledPage]:
    """
    Run the crawl and write its results. A root-fetch failure marks the
    whole source 'failed' with the error recorded; otherwise every crawled
    page is upserted (new pages inserted, changed pages updated, unchanged
    pages left alone), then every page is chunked and embedded, and only if
    that succeeds too is the source marked 'completed'. An embedding
    failure after a successful crawl still marks the source 'failed' - the
    crawled pages stay on record (they are real), but the chunk set is left
    untouched rather than replaced with an incomplete one.
    """

    try:
        crawl_results = await crawl_website(fetcher, url)
    except PageFetchError as exc:
        knowledge_source.status = FAILED_STATUS
        knowledge_source.error_message = str(exc)
        await db.flush()

        return await crawled_page_repo.list_for_source(db, knowledge_source.id)

    for result in crawl_results:
        await crawled_page_repo.upsert(
            db,
            knowledge_source_id=knowledge_source.id,
            url=result.url,
            extracted_text=result.extracted_text,
            content_hash=result.content_hash,
            fetched_at=result.fetched_at,
        )

    spans_by_url: list[tuple[str, ChunkSpan]] = []
    for result in sorted(crawl_results, key=lambda r: r.url):
        for span in chunk_text(result.extracted_text):
            spans_by_url.append((result.url, span))

    try:
        vectors = await embedding_provider.embed(
            [span.text for _url, span in spans_by_url]
        )
    except EmbeddingProviderError as exc:
        knowledge_source.status = FAILED_STATUS
        knowledge_source.error_message = str(exc)
        await db.flush()

        return await crawled_page_repo.list_for_source(db, knowledge_source.id)

    await chunk_repo.replace_for_source(
        db,
        organization_id=knowledge_source.organization_id,
        workspace_id=knowledge_source.workspace_id,
        assistant_id=knowledge_source.assistant_id,
        knowledge_source_id=knowledge_source.id,
        chunks=[
            ChunkWrite(
                text=span.text,
                metadata={
                    "url": page_url,
                    "char_start": span.char_start,
                    "char_end": span.char_end,
                },
                embedding=vector,
            )
            for (page_url, span), vector in zip(spans_by_url, vectors, strict=True)
        ],
    )

    knowledge_source.status = COMPLETED_STATUS
    knowledge_source.error_message = None
    await db.flush()

    return await crawled_page_repo.list_for_source(db, knowledge_source.id)


async def create_website_knowledge_source(
    db: AsyncSession,
    fetcher: PageFetcher,
    embedding_provider: EmbeddingProvider,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    assistant_id: uuid.UUID,
    owner_user_id: uuid.UUID,
    url: str,
) -> tuple[KnowledgeSource, list[CrawledPage]]:
    """
    Create a new website-type knowledge source and run its first crawl.
    """

    await _resolve_workspace_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )
    await assistant_service.get_assistant(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
    )

    knowledge_source = KnowledgeSource(
        organization_id=organization_id,
        workspace_id=workspace_id,
        assistant_id=assistant_id,
        type=knowledge_source_repo.WEBSITE_TYPE,
        owner_user_id=owner_user_id,
        source_url=url,
    )
    db.add(knowledge_source)
    await db.flush()

    crawled_pages = await _crawl_and_reconcile(
        db, fetcher, embedding_provider, knowledge_source, url
    )

    return knowledge_source, crawled_pages


async def recrawl_knowledge_source(
    db: AsyncSession,
    fetcher: PageFetcher,
    embedding_provider: EmbeddingProvider,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
) -> tuple[KnowledgeSource, list[CrawledPage]]:
    """
    Re-run the crawl for an existing website-type knowledge source, using
    its stored source_url.
    """

    knowledge_source = await resolve_knowledge_source(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        knowledge_source_id=knowledge_source_id,
    )

    if (
        knowledge_source.type != knowledge_source_repo.WEBSITE_TYPE
        or not knowledge_source.source_url
    ):
        raise KnowledgeSourceNotFound

    crawled_pages = await _crawl_and_reconcile(
        db, fetcher, embedding_provider, knowledge_source, knowledge_source.source_url
    )

    return knowledge_source, crawled_pages
