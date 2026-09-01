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
    ambient_sound_volume: float | None = Field(default=None, ge=0.0, le=1.0)
    # Configuration-only until real call handling (build-plan items 24-28)
    # exists to enforce them - see current-feature.md's Architecture
    # decisions. Bounds are sanity checks, not tuned product values.
    max_call_duration_seconds: int | None = Field(default=None, ge=30, le=3600)
    max_silence_timeout_seconds: int | None = Field(default=None, ge=5, le=300)
    record_calls: bool = False
    auto_delete_on_declined_consent: bool = False
    custom_prompt: str | None = Field(default=None, max_length=8000)


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
    ambient_sound_volume: float | None
    max_call_duration_seconds: int | None
    max_silence_timeout_seconds: int | None
    record_calls: bool
    auto_delete_on_declined_consent: bool
    custom_prompt: str | None
    created_at: datetime


class AssistantVersionFieldDiff(BaseModel):
    previous: Any
    current: Any


class AssistantVersionDiffResponse(BaseModel):
    from_version: int
    to_version: int
    changes: dict[str, AssistantVersionFieldDiff]
