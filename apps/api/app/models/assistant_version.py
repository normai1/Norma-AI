import uuid

from sqlalchemy import ForeignKey, Numeric, String, Text, UniqueConstraint, event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.exceptions import AssistantVersionImmutable
from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class AssistantVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    An immutable configuration snapshot for an assistant. Nothing in this
    codebase updates an existing row yet - 11c is where that guarantee gets
    enforced in code, not just by convention.
    """

    __tablename__ = "assistant_versions"

    __table_args__ = (
        UniqueConstraint(
            "assistant_id",
            "version",
            name="uq_assistant_versions_assistant_version",
        ),
    )

    assistant_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistants.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version: Mapped[int] = mapped_column(nullable=False)

    voice_id: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str] = mapped_column(String(32), nullable=False)
    greeting: Mapped[str] = mapped_column(Text, nullable=False)
    persona: Mapped[str | None] = mapped_column(Text, nullable=True)

    speech_rate: Mapped[float] = mapped_column(
        Numeric(3, 2, asdecimal=False),
        nullable=False,
    )
    turn_sensitivity: Mapped[float] = mapped_column(
        Numeric(3, 2, asdecimal=False),
        nullable=False,
    )
    creativity: Mapped[float] = mapped_column(
        Numeric(3, 2, asdecimal=False),
        nullable=False,
    )

    ambient_sound: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Which prompt template (and which version of it) this configuration
    # snapshot was based on - both nullable, both-or-neither (enforced in
    # the schema, not here). No ondelete cascade: deleting a prompt template
    # is not a capability that exists yet, and must never cascade-delete an
    # assistant version that references it.
    prompt_template_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "prompt_templates.id",
            name="fk_assistant_versions_prompt_template_id",
        ),
        nullable=True,
    )

    prompt_version: Mapped[int | None] = mapped_column(nullable=True)


@event.listens_for(AssistantVersion, "before_update")
def _reject_update(mapper, connection, target: AssistantVersion) -> None:
    """
    Enforce immutability structurally, not just by never writing an update
    function - nothing in this codebase attempts one today, so this turns
    that absence into a guarantee a future call site can't accidentally
    break.
    """

    raise AssistantVersionImmutable(
        f"AssistantVersion {target.id} is immutable and cannot be updated",
    )
