import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.organization import OrganizationRole


class InvitationCreate(BaseModel):
    email: EmailStr
    role: OrganizationRole = "member"


class InvitationAccept(BaseModel):
    token: str = Field(min_length=1, max_length=512)


class InvitationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    organization_id: uuid.UUID
    email: EmailStr
    role: str
    status: str
    expires_at: datetime
    created_at: datetime


class InvitationCreatedResponse(InvitationResponse):
    """
    The invitation plus its plaintext token.

    The token is only ever returned here, at creation. It is not readable from
    the list endpoint, and only the hash is stored.
    """

    token: str
