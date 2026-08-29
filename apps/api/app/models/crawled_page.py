import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class CrawledPage(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    One page of a 'website'-type KnowledgeSource. One row per URL, updated
    in place across recrawls - not an immutable version history the way
    AssistantVersion/PromptVersion are. content_hash is the dedup key: a
    recrawl only rewrites extracted_text when the hash actually changes.
    """

    __tablename__ = "crawled_pages"

    __table_args__ = (
        UniqueConstraint(
            "knowledge_source_id",
            "url",
            name="uq_crawled_pages_knowledge_source_url",
        ),
    )

    knowledge_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    url: Mapped[str] = mapped_column(String(2048), nullable=False)

    # Distinct from TimestampMixin's created_at/updated_at: fetched_at marks
    # when the page's content was last verified against the live site,
    # refreshed on every recrawl attempt even when the hash is unchanged.
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    extracted_text: Mapped[str] = mapped_column(Text, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
