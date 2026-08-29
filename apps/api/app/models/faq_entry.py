import uuid

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class FaqEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    One operator-authored question/answer pair belonging to a
    'manual_faq'-type KnowledgeSource. Many entries per source (unlike
    GlossaryEntry's per-assistant shape) - matches CrawledPage's
    one-source-to-many relationship.
    """

    __tablename__ = "faq_entries"

    knowledge_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
