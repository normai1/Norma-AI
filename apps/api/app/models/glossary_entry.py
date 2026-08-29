import uuid

from sqlalchemy import ForeignKey, Numeric, String, Text, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class GlossaryEntry(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    A per-assistant term with an optional meaning and phonetic pronunciation
    override - feeds STT keyword biasing and TTS pronunciation once item 20's
    real-time voice engine exists to call them. A plain reference row, not a
    versioned configuration snapshot: there is no archive/publish lifecycle,
    and deleting a row really deletes it.
    """

    __tablename__ = "glossary_entries"

    __table_args__ = (
        UniqueConstraint(
            "assistant_id",
            "term",
            name="uq_glossary_entries_assistant_term",
        ),
    )

    # organization_id/workspace_id are denormalized - assistant_id is the
    # real owning scope, but every tenant-scoped query in this codebase
    # filters directly on org/workspace, the same convention Chunk uses.
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

    assistant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    term: Mapped[str] = mapped_column(String(255), nullable=False)
    meaning: Mapped[str | None] = mapped_column(Text, nullable=True)
    phonetic_spelling: Mapped[str | None] = mapped_column(String(255), nullable=True)

    stt_boost_weight: Mapped[float] = mapped_column(
        Numeric(3, 2, asdecimal=False),
        nullable=False,
        server_default=text("0.5"),
    )
