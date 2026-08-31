from pydantic import BaseModel


class VoiceSessionTicketResponse(BaseModel):
    ticket: str
    expires_in: int
