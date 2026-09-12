import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Integer
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

    call_id has no foreign key yet - Call (build-plan item 28) doesn't
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

    # The turn's own identity, minted by apps/voice before the turn runs and
    # stamped on every log line that turn produces (item 25b,
    # norma_shared.correlation) - which is the whole point of not simply
    # using this row's primary key: a log line written mid-turn cannot carry
    # an id the database has not issued yet.
    #
    # Nullable, and not unique. Nullable because this column arrived after
    # rows already existed and a migration must be additive across a deploy
    # where the two planes run different code (CLAUDE.md section 6.2) - an
    # older voice worker sends no turn_id and its rows are still valid
    # metrics. Not unique because a uniqueness violation would reject a
    # turn's metrics over an identifier that is diagnostic only; a duplicate
    # would be a bug in the sender, visible in the data, not something worth
    # failing a write over in the audio path's own request.
    turn_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
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

    # What this turn's realtime LLM call cost (item 25b). CLAUDE.md section
    # 21: "Capture provider cost per call from day one. Gross margin per
    # minute determines whether this business works." A call's totals are
    # the sum over its turns; the turn is the granularity at which an
    # unexpectedly expensive answer can actually be found.
    #
    # All three are nullable and all three mean "not known", never zero. A
    # provider that reports no usage, a model nobody has priced, and a turn
    # that never reached the LLM are all genuinely unknown, and recording
    # any of them as free would understate cost invisibly - see
    # norma_shared.token_cost.
    prompt_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    completion_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Millionths of a US dollar, as an integer. Never a float: these are
    # summed into invoices, and binary floating point cannot hold a tenth of
    # a cent exactly. BigInteger because the natural next step is summing
    # this column over an organization's history, and a 32-bit total would
    # overflow at about $2,147 of accumulated spend.
    cost_micro_usd: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
