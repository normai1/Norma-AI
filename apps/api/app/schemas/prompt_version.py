import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PromptVersionCreate(BaseModel):
    content: str = Field(min_length=1, max_length=20000)


class PromptVersionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    prompt_template_id: uuid.UUID
    version: int
    content: str
    created_at: datetime


class PromptVersionFieldDiff(BaseModel):
    previous: Any
    current: Any


class PromptVersionDiffResponse(BaseModel):
    from_version: int
    to_version: int
    changes: dict[str, PromptVersionFieldDiff]
