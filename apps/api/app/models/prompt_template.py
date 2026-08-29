import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class PromptTemplate(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    A reusable, named prompt template, scoped to one workspace. Content
    itself lives in versioned snapshots (`PromptVersion`); this table only
    tracks identity, use-case label, lifecycle status, and which version is
    live - the same split `Assistant`/`AssistantVersion` uses.
    """

    __tablename__ = "prompt_templates"

    __table_args__ = (
        CheckConstraint(
            "status IN ('draft', 'published', 'archived')",
            name="ck_prompt_templates_status",
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

    # Free-form, not a DB enum - CLAUDE.md and the build plan name six
    # examples (receptionist, support, scheduling, answering machine, field
    # service, order intake) but neither closes the set.
    use_case: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'draft'"),
    )

    # Nullable and unset by everything before /publish exists. No ondelete
    # cascade: deleting a version (not a capability that exists yet either)
    # must never cascade-delete the template that happens to point at it.
    # use_alter breaks the circular FK dependency (prompt_versions.
    # prompt_template_id -> prompt_templates.id, prompt_templates.
    # current_version_id -> prompt_versions.id) so both SQLAlchemy's
    # create_all and Alembic can order table creation.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "prompt_versions.id",
            use_alter=True,
            name="fk_prompt_templates_current_version_id",
        ),
        nullable=True,
    )
