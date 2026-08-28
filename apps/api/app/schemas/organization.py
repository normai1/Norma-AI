import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.settings import OrganizationSettings, OrganizationSettingsUpdate

# Mirrors ORGANIZATION_ROLES in the model. A Literal rather than a regex so
# matching is exact: an unanchored pattern would let 'xowner' through to the
# database CHECK constraint and surface as a 500 instead of a 422.
OrganizationRole = Literal["owner", "admin", "member", "viewer"]


class OrganizationCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class OrganizationUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    settings: OrganizationSettingsUpdate | None = None


class OrganizationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    slug: str
    settings: OrganizationSettings
    status: str
    created_at: datetime


class OrganizationMembershipResponse(OrganizationResponse):
    """
    An organization plus the calling user's role in it.
    """

    role: str


class MemberUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str | None
    avatar_url: str | None


class MemberResponse(BaseModel):
    id: uuid.UUID
    role: str
    created_at: datetime
    user: MemberUserResponse


class MemberRoleUpdate(BaseModel):
    role: OrganizationRole
