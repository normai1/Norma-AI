import uuid

from sqlalchemy import CheckConstraint, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base_class import Base
from app.models.mixins import TimestampMixin, UUIDPrimaryKeyMixin

# Ordered most privileged first. Feature 3 builds the real permission model on
# top of these values; this feature only needs to know they are valid.
ORGANIZATION_ROLES = ("owner", "admin", "member", "viewer")

# Lower rank means more privileged. Used to stop a member granting a role above
# their own or acting on someone who outranks them.
ROLE_RANK = {role: rank for rank, role in enumerate(ORGANIZATION_ROLES)}


def outranks(role: str, other: str) -> bool:
    """
    True when `role` is strictly more privileged than `other`.
    """

    return ROLE_RANK[role] < ROLE_RANK[other]


class OrganizationMember(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    """
    One user's membership of one organization, carrying their role in it.
    """

    __tablename__ = "organization_members"

    __table_args__ = (
        UniqueConstraint(
            "organization_id",
            "user_id",
            name="uq_organization_members_org_user",
        ),
        CheckConstraint(
            "role IN ('owner', 'admin', 'member', 'viewer')",
            name="ck_organization_members_role",
        ),
    )

    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("organizations.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    role: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
