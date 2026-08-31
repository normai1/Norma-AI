import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class TurnMetric(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    One row per conversational turn, across every leg of the voice pipeline
    (item 20f) - written once, at the moment the turn concludes, never
    incrementally (CLAUDE.md section 6.5: no one-row-per-statement writes
    in the audio path). A leg the turn never reached (a failure, a
    barge-in, a mid-reply disconnect) simply stays null.

    call_id has no foreign key yet - Call (build-plan item 27) doesn't
    exist. apps/voice generates a session-scoped UUID to stand in until
    then; adding the constraint later is a purely additive migration.
    organization_id/workspace_id/assistant_id are denormalized directly
    rather than left to a future join through Call, mirroring Chunk's and
    GlossaryEntry's own precedent for a row whose "real" owning scope is
    one level removed.
    """

    __tablename__ = "turn_metrics"

    __table_args__ = (
        # Backs list_since()'s time-range query - CLAUDE.md section 6.5:
        # "Index for the queries the call list and analytics actually run."
        Index("ix_turn_metrics_created_at", "created_at"),
    )

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

    call_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), nullable=False, index=True
    )

    stt_finalized_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    retrieval_done_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    llm_first_token_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    llm_complete_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    tts_first_byte_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    audio_out_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
