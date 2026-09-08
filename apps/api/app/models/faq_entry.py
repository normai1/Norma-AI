import uuid

from sqlalchemy import ForeignKey, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class FaqEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    One question/answer pair belonging to a 'manual_faq'-type
    KnowledgeSource. Many entries per source (unlike GlossaryEntry's
    per-assistant shape) - matches CrawledPage's one-source-to-many
    relationship.

    Two things can create one: an operator typing it, or FAQ generation
    reading an uploaded file or crawled site. Both live in the same
    manual_faq container, which is why knowledge_source_id alone cannot
    say where an entry came from - see generated_from_knowledge_source_id.
    """

    __tablename__ = "faq_entries"

    knowledge_source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # The file or website source this entry was generated from, or NULL
    # when an operator wrote it by hand.
    #
    # Generated entries do NOT live under the source they came from - every
    # one of an assistant's generated entries shares a single manual_faq
    # container (faq_generation._get_or_create_generated_faq_source), so
    # before this column there was no way back to the origin at all. That
    # left generated entries surviving the deletion of the very document
    # they were written from, still embedded and still answering calls.
    #
    # ondelete CASCADE is a backstop, not the mechanism: the chunk backing
    # each entry is linked only by a metadata key, not a foreign key, so it
    # cannot cascade and has to be removed explicitly by the delete path in
    # services/knowledge_source.py. The cascade only matters if some future
    # deletion path forgets to do that.
    generated_from_knowledge_source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("knowledge_sources.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )

    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=False)
