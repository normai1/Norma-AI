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

    # Item 23d: nullable at the DB level (a small number of rows created
    # before this feature existed have no sensible assistant to assign),
    # but required by every creation path going forward - see
    # current-feature.md's Architecture decisions. NULL means "not usable
    # by any assistant's retrieval," not "shared by all."
    assistant_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("assistants.id", ondelete="CASCADE"),
        nullable=True,
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

    # Only ever set for type='website' rows - the domain/page a crawl
    # started from, remembered so a recrawl doesn't need it re-supplied.
    # Not in project-overview.md's original locked schema (written before
    # this feature's detailed design); reconciled here as an additive column
    # rather than silently guessed at (feature 15's own spec note).
    source_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)

    # Only ever set for type='manual_faq' rows - the operator-facing label
    # distinguishing one FAQ set from another (e.g. "General FAQ" vs.
    # "Billing Questions"), the same class of reconciliation as source_url
    # above (feature 16's own spec note).
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
