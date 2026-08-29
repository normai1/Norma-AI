import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    filename: str
    content_type: str
    processing_status: str
    processing_error: str | None
    created_at: datetime


class CrawledPageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    fetched_at: datetime
    content_hash: str


class WebsiteKnowledgeSourceCreate(BaseModel):
    url: HttpUrl


class ManualFaqKnowledgeSourceCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class KnowledgeSourceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    type: str
    status: str
    error_message: str | None
    owner_user_id: uuid.UUID | None
    source_url: str | None = None
    name: str | None = None
    created_at: datetime
    document: DocumentResponse | None = None
    crawled_pages: list[CrawledPageResponse] | None = None
