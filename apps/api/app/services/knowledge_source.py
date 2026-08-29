import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import (
    FileTooLarge,
    KnowledgeSourceNotFound,
    UnsupportedFileType,
    WorkspaceNotFound,
)
from app.models.document import Document
from app.models.knowledge_source import KnowledgeSource
from app.providers.storage import StorageProvider
from app.repositories import knowledge_source as knowledge_source_repo
from app.repositories import workspace as workspace_repo

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


async def upload_knowledge_source(
    db: AsyncSession,
    storage: StorageProvider,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
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
            owner_user_id=owner_user_id,
            filename=filename,
            content_type=content_type,
            storage_key=key,
        )
    except Exception:
        await storage.delete(key)
        raise

    return knowledge_source, document


async def list_knowledge_sources(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
) -> list[tuple[KnowledgeSource, Document | None]]:
    """
    Every knowledge source in a workspace, paired with its document.
    """

    await _resolve_workspace_id(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
    )

    knowledge_sources = await knowledge_source_repo.list_for_workspace(db, workspace_id)
    documents = await knowledge_source_repo.list_documents_for_sources(
        db,
        [source.id for source in knowledge_sources],
    )
    documents_by_source_id = {
        document.knowledge_source_id: document for document in documents
    }

    return [
        (source, documents_by_source_id.get(source.id)) for source in knowledge_sources
    ]


async def get_knowledge_source(
    db: AsyncSession,
    *,
    organization_id: uuid.UUID,
    workspace_id: uuid.UUID,
    knowledge_source_id: uuid.UUID,
) -> tuple[KnowledgeSource, Document | None]:
    """
    Fetch one knowledge source the caller may access, paired with its
    document.
    """

    knowledge_source = await resolve_knowledge_source(
        db,
        organization_id=organization_id,
        workspace_id=workspace_id,
        knowledge_source_id=knowledge_source_id,
    )
    document = await knowledge_source_repo.get_document_for_source(
        db,
        knowledge_source.id,
    )

    return knowledge_source, document
