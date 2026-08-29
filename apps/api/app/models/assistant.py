import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class Assistant(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    A configurable AI phone assistant, scoped to one workspace. Configuration
    itself lives in versioned snapshots (`AssistantVersion`); this table only
    tracks identity, lifecycle status, and which version is live.
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

    # Nullable and unset by everything in 11b - only 11c's /publish action
    # ever writes this. No ondelete cascade: deleting a version (not a
    # capability that exists yet either) must never cascade-delete the
    # assistant that happens to point at it. use_alter breaks the circular
    # FK dependency (assistant_versions.assistant_id -> assistants.id,
    # assistants.current_version_id -> assistant_versions.id) so both
    # SQLAlchemy's create_all and Alembic can order table creation.
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey(
            "assistant_versions.id",
            use_alter=True,
            name="fk_assistants_current_version_id",
        ),
        nullable=True,
    )
