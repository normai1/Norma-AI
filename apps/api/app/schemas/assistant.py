import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AssistantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class AssistantUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class AssistantPublish(BaseModel):
    version: int = Field(ge=1)


class AssistantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    status: str
    current_version_id: uuid.UUID | None
    created_at: datetime
