import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class PromptTemplateCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    use_case: str = Field(min_length=1, max_length=100)


class PromptTemplateUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class PromptTemplatePublish(BaseModel):
    version: int = Field(ge=1)


class PromptTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    use_case: str
    status: str
    current_version_id: uuid.UUID | None
    created_at: datetime
