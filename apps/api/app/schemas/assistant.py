import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AssistantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class AssistantUpdate(BaseModel):
    """
    A partial update - every field is optional, and only the ones actually
    provided are changed. Mirrors PATCH semantics exactly: renaming an
    assistant and editing its voice no longer need separate endpoints, since
    there is no version snapshot to create - this writes the one mutable
    Assistant row directly.
    """

    name: str | None = Field(default=None, min_length=1, max_length=255)
    voice_id: str | None = Field(default=None, min_length=1, max_length=255)
    language: str | None = Field(default=None, min_length=1, max_length=32)
    greeting: str | None = Field(default=None, min_length=1, max_length=2000)
    persona: str | None = Field(default=None, max_length=4000)
    custom_prompt: str | None = Field(default=None, max_length=8000)
    speech_rate: float | None = Field(default=None, ge=0.5, le=2.0)
    turn_sensitivity: float | None = Field(default=None, ge=0.0, le=1.0)
    creativity: float | None = Field(default=None, ge=0.0, le=1.0)
    ambient_sound: str | None = Field(default=None, max_length=255)
    ambient_sound_volume: float | None = Field(default=None, ge=0.0, le=1.0)
    # Configuration-only until real call handling (build-plan items 24-28)
    # exists to enforce them.
    max_call_duration_seconds: int | None = Field(default=None, ge=30, le=3600)
    max_silence_timeout_seconds: int | None = Field(default=None, ge=5, le=300)
    record_calls: bool | None = None
    auto_delete_on_declined_consent: bool | None = None


class AssistantResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    workspace_id: uuid.UUID
    name: str
    status: str
    voice_id: str | None
    language: str | None
    greeting: str | None
    persona: str | None
    custom_prompt: str | None
    speech_rate: float
    turn_sensitivity: float
    creativity: float
    ambient_sound: str | None
    ambient_sound_volume: float | None
    max_call_duration_seconds: int | None
    max_silence_timeout_seconds: int | None
    record_calls: bool
    auto_delete_on_declined_consent: bool
    created_at: datetime
