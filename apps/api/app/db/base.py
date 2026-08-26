"""
Model registry for Alembic.

`Base` lives in `base_class` so models can import it without a circular import.
Importing every model here is what populates `Base.metadata`, which Alembic
autogenerate reads. A model that is not imported here is invisible to migrations.
"""

from app.db.base_class import Base
from app.models.invitation import Invitation
from app.models.organization import Organization
from app.models.organization_member import OrganizationMember
from app.models.session import UserSession
from app.models.user import User

__all__ = [
    "Base",
    "Invitation",
    "Organization",
    "OrganizationMember",
    "User",
    "UserSession",
]
