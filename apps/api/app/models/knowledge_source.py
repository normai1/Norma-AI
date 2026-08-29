import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, Text, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin


class KnowledgeSource(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    A source of knowledge an assistant answers from - a file upload, a
    crawled website, or a manually authored FAQ set. This feature (item 14)
    only ever creates 'file' rows; 'website' (item 15) and 'manual_faq'
    (item 16) are part of the same locked type set but have no creation path
    yet. status/error_message are stored and surfaced here; nothing
    transitions status past 'pending' until item 17's parsing pipeline
    exists.
    """

    __tablename__ = "knowledge_sources"

    __table_args__ = (
        CheckConstraint(
            "type IN ('file', 'website', 'manual_faq')",
            name="ck_knowledge_sources_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'processing', 'completed', 'failed')",
            name="ck_knowledge_sources_status",
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

    type: Mapped[str] = mapped_column(String(20), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        server_default=text("'pending'"),
    )

    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Attribution only - "who uploaded this", not an ownership row whose
    # existence depends on the user (unlike OrganizationMember/
    # WorkspaceMember). Deleting the user must never delete the workspace's
    # knowledge base; SET NULL matches Invitation.invited_by_user_id's exact
    # precedent for this class of field.
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
