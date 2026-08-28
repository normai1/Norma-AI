import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.organization import MemberUserResponse
from app.schemas.settings import WorkspaceSettings, WorkspaceSettingsUpdate


class WorkspaceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class WorkspaceUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    settings: WorkspaceSettingsUpdate | None = None


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    name: str
    settings: WorkspaceSettings
    created_at: datetime


class WorkspaceMemberCreate(BaseModel):
    member_id: uuid.UUID


class WorkspaceMemberResponse(BaseModel):
    id: uuid.UUID
    workspace_id: uuid.UUID
    created_at: datetime
    user: MemberUserResponse
