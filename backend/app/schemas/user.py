"""User-facing representations of the User document."""

from __future__ import annotations

from datetime import datetime

from beanie import PydanticObjectId
from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import UserRole
from app.models.user import TravelPreferences


class UserPublic(BaseModel):
    """What a user is allowed to see about themselves.

    Deliberately excludes ``hashed_password``; building the response from an
    explicit schema rather than dumping the document is what guarantees that.
    """

    model_config = ConfigDict(from_attributes=True)

    id: PydanticObjectId = Field(validation_alias="id")
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool
    is_verified: bool
    home_city: str | None = None
    avatar_url: str | None = None
    preferences: TravelPreferences
    created_at: datetime
    last_login_at: datetime | None = None


class UserUpdate(BaseModel):
    """Partial update. Unset fields are left untouched."""

    full_name: str | None = Field(default=None, min_length=1, max_length=200)
    home_city: str | None = Field(default=None, max_length=200)
    preferences: TravelPreferences | None = None
