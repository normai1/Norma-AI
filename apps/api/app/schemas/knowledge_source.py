import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    processing_status: str
    processing_error: str | None
    created_at: datetime


class KnowledgeSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    type: str
    status: str
    error_message: str | None
    owner_user_id: uuid.UUID | None
    created_at: datetime
    document: DocumentResponse | None = None
