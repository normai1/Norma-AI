import uuid

from sqlalchemy import ForeignKey, Text, UniqueConstraint, event
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.exceptions import PromptVersionImmutable
from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class PromptVersion(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    An immutable content snapshot for a prompt template, enforced by the
    `before_update` listener below - mirrors `AssistantVersion` exactly.
    """

    __tablename__ = "prompt_versions"

    __table_args__ = (
        UniqueConstraint(
            "prompt_template_id",
            "version",
            name="uq_prompt_versions_prompt_template_version",
        ),
    )

    prompt_template_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("prompt_templates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    version: Mapped[int] = mapped_column(nullable=False)

    content: Mapped[str] = mapped_column(Text, nullable=False)


@event.listens_for(PromptVersion, "before_update")
def _reject_update(mapper, connection, target: PromptVersion) -> None:
    """
    Enforce immutability structurally, not just by never writing an update
    function - nothing in this codebase attempts one today, so this turns
    that absence into a guarantee a future call site can't accidentally
    break.
    """

    raise PromptVersionImmutable(
        f"PromptVersion {target.id} is immutable and cannot be updated",
    )
