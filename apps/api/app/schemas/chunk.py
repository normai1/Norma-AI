import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ChunkResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    knowledge_source_id: uuid.UUID
    text: str
    ordering: int
    # The ORM attribute is chunk_metadata (metadata is reserved on the
    # declarative Base) - validation_alias reads it back under the API's
    # own metadata name.
    metadata: dict[str, Any] = Field(validation_alias="chunk_metadata")
    created_at: datetime
