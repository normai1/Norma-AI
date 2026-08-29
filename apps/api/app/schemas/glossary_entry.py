import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class GlossaryEntryCreate(BaseModel):
    term: str = Field(min_length=1, max_length=255)
    meaning: str | None = Field(default=None, max_length=2000)
    phonetic_spelling: str | None = Field(default=None, max_length=255)
    stt_boost_weight: float = Field(default=0.5, ge=0.0, le=1.0)


class GlossaryEntryUpdate(BaseModel):
    term: str | None = Field(default=None, min_length=1, max_length=255)
    meaning: str | None = Field(default=None, max_length=2000)
    phonetic_spelling: str | None = Field(default=None, max_length=255)
    stt_boost_weight: float | None = Field(default=None, ge=0.0, le=1.0)


class GlossaryEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    assistant_id: uuid.UUID
    term: str
    meaning: str | None
    phonetic_spelling: str | None
    stt_boost_weight: float
    created_at: datetime
