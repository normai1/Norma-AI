"""
Model registry for Alembic.

`Base` lives in `base_class` so models can import it without a circular import.
Importing every model here is what populates `Base.metadata`, which Alembic
autogenerate reads. A model that is not imported here is invisible to migrations.
"""

from app.db.base_class import Base
from app.models.assistant import Assistant
from app.models.assistant_version import AssistantVersion
from app.models.glossary_entry import GlossaryEntry
from app.models.invitation import Invitation
from app.models.organization import Organization
from app.models.organization_member import OrganizationMember
from app.models.prompt_template import PromptTemplate
from app.models.prompt_version import PromptVersion
from app.models.session import UserSession
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember

__all__ = [
    "Assistant",
    "AssistantVersion",
    "Base",
    "GlossaryEntry",
    "Invitation",
    "Organization",
    "OrganizationMember",
    "PromptTemplate",
    "PromptVersion",
    "User",
    "UserSession",
    "Workspace",
    "WorkspaceMember",
]
