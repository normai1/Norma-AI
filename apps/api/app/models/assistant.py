import uuid

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Assistant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    A configurable AI phone assistant, scoped to one workspace. A single
    mutable row - editing an assistant updates it in place; there is no
    separate immutable-snapshot/version history (a deliberate simplification
    that removed the earlier AssistantVersion system: "just edit the
    assistant, nothing else"). voice_id/language/greeting stay nullable
    since a freshly created assistant has none of them configured yet;
    speech_rate/turn_sensitivity/creativity carry real DB defaults so every
    assistant always has valid numeric config, even unconfigured ones.
    """

    __tablename__ = "assistants"

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_assistants_status",
        ),
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

    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'draft'"),
    )

    voice_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    language: Mapped[str | None] = mapped_column(String(32), nullable=True)
    greeting: Mapped[str | None] = mapped_column(Text, nullable=True)
    persona: Mapped[str | None] = mapped_column(Text, nullable=True)
    custom_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)

    speech_rate: Mapped[float] = mapped_column(
        Numeric(3, 2, asdecimal=False),
        nullable=False,
        server_default=text("1.0"),
    )
    turn_sensitivity: Mapped[float] = mapped_column(
        Numeric(3, 2, asdecimal=False),
        nullable=False,
        server_default=text("0.5"),
    )
    creativity: Mapped[float] = mapped_column(
        Numeric(3, 2, asdecimal=False),
        nullable=False,
        server_default=text("0.3"),
    )

    ambient_sound: Mapped[str | None] = mapped_column(String(255), nullable=True)
    ambient_sound_volume: Mapped[float | None] = mapped_column(
        Numeric(3, 2, asdecimal=False),
        nullable=True,
    )

    max_call_duration_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    max_silence_timeout_seconds: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    record_calls: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    auto_delete_on_declined_consent: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
