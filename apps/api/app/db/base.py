"""
Model registry for Alembic.

`Base` lives in `base_class` so models can import it without a circular import.
Importing every model here is what populates `Base.metadata`, which Alembic
autogenerate reads. A model that is not imported here is invisible to migrations.
"""

from app.db.base_class import Base
from app.models.assistant import Assistant
from app.models.chunk import Chunk
from app.models.crawled_page import CrawledPage
from app.models.document import Document
from app.models.faq_entry import FaqEntry
from app.models.glossary_entry import GlossaryEntry
from app.models.invitation import Invitation
from app.models.knowledge_source import KnowledgeSource
from app.models.organization import Organization
from app.models.organization_member import OrganizationMember
from app.models.session import UserSession
from app.models.turn_metric import TurnMetric
from app.models.user import User
from app.models.workspace import Workspace
from app.models.workspace_member import WorkspaceMember

__all__ = [
    "Assistant",
    "Base",
    "Chunk",
    "CrawledPage",
    "Document",
    "FaqEntry",
    "GlossaryEntry",
    "Invitation",
    "KnowledgeSource",
    "Organization",
    "OrganizationMember",
    "TurnMetric",
    "User",
    "UserSession",
    "Workspace",
    "WorkspaceMember",
]
