import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class FaqEntryCreate(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    answer: str = Field(min_length=1, max_length=4000)


class FaqEntryUpdate(BaseModel):
    question: str | None = Field(default=None, min_length=1, max_length=2000)
    answer: str | None = Field(default=None, min_length=1, max_length=4000)


class FaqEntryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    knowledge_source_id: uuid.UUID
    question: str
    answer: str
    created_at: datetime

    # The file or website source this entry was generated from, or None for
    # an operator-authored one. Only the id: the client already holds the
    # assistant's sources to render the list, so it resolves the display name
    # through its own knowledgeSourceDisplayName() - one naming rule for the
    # whole UI, and no per-entry lookup here.
    generated_from_knowledge_source_id: uuid.UUID | None = None
