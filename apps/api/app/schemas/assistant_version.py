import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class AssistantVersionCreate(BaseModel):
    voice_id: str = Field(min_length=1, max_length=255)
    language: str = Field(min_length=1, max_length=32)
    greeting: str = Field(min_length=1, max_length=2000)
    persona: str | None = Field(default=None, max_length=4000)
    speech_rate: float = Field(default=1.0, ge=0.5, le=2.0)
    turn_sensitivity: float = Field(default=0.5, ge=0.0, le=1.0)
    creativity: float = Field(default=0.3, ge=0.0, le=1.0)
    ambient_sound: str | None = Field(default=None, max_length=255)


class AssistantVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    assistant_id: uuid.UUID
    version: int
    voice_id: str
    language: str
    greeting: str
    persona: str | None
    speech_rate: float
    turn_sensitivity: float
    creativity: float
    ambient_sound: str | None
    created_at: datetime


class AssistantVersionFieldDiff(BaseModel):
    previous: Any
    current: Any


class AssistantVersionDiffResponse(BaseModel):
    from_version: int
    to_version: int
    changes: dict[str, AssistantVersionFieldDiff]
