import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Integer, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

EMBEDDING_DIMENSION = 1536


class Chunk(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    One retrievable unit of a KnowledgeSource's content, produced by item
    17's parsing/chunking pipeline. organization_id/workspace_id are
    denormalized here (not just reachable via knowledge_source_id) because
    retrieval (item 19) filters directly on them on the hot in-call path.
    embedding is created now (the full locked shape) but stays NULL until
    item 18 - never write to it from this feature.
    """

    __tablename__ = "chunks"

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    knowledge_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    text: Mapped[str] = mapped_column(Text, nullable=False)

    ordering: Mapped[int] = mapped_column(Integer, nullable=False)

    # Python attribute name can't be `metadata` - SQLAlchemy's declarative
    # Base reserves that name for its own MetaData registry. The DB column
    # is still named `metadata`, matching project-overview.md's locked
    # Chunk contract. Doubles as citation traceability (page/section/offset)
    # and, for a manual_faq chunk, the {"faq_entry_id": "<uuid>"} lookup key
    # back to its owning FaqEntry.
    chunk_metadata: Mapped[dict[str, Any]] = mapped_column(
        "metadata",
        JSONB,
        nullable=False,
        server_default=sql_text("'{}'::jsonb"),
    )

    embedding: Mapped[list[float] | None] = mapped_column(
        Vector(EMBEDDING_DIMENSION),
        nullable=True,
    )
