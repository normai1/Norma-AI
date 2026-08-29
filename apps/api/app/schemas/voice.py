from pydantic import BaseModel, ConfigDict


class VoiceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    language: str
    gender: str | None
    preview_url: str | None
