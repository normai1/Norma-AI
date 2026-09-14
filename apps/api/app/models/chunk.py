import uuid
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import ForeignKey, Index, Integer, Text
from sqlalchemy import text as sql_text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.config import settings
from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

EMBEDDING_DIMENSION = settings.embedding_dimension


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

    # Item 23d: denormalized from the parent KnowledgeSource, mirroring
    # organization_id/workspace_id's own precedent above - retrieval
    # filters directly on it on the hot in-call path. Set once at
    # chunk-write time by reading knowledge_source.assistant_id, never
    # threaded independently, so it can never drift from its source.
    assistant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistants.id", ondelete="CASCADE"),
        nullable=True,
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

    __table_args__ = (
        # Without this, every retrieval is a sequential scan that computes a
        # cosine distance for each stored vector in turn. Measured on 10,141
        # chunks: 1,847ms for one top-5 query, which is the whole per-turn
        # latency budget spent before the model has seen anything.
        #
        # HNSW rather than IVFFlat: it needs no training pass over existing
        # data, so it stays correct as chunks are added and replaced, and it
        # is the better recall/latency trade at this size. vector_cosine_ops
        # because retrieval orders by cosine distance - an index built for a
        # different operator is simply not used, silently, and the scan comes
        # back.
        Index(
            "ix_chunks_embedding_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )
