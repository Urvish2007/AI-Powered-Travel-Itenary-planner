"""Authentication request and response bodies."""

from __future__ import annotations

import re

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.models.user import TravelPreferences
from app.schemas.user import UserPublic

# Minimum viable password policy: length does most of the work, the character
# classes stop the obvious "password"/"11111111" cases.
MIN_PASSWORD_LENGTH = 10


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)
    full_name: str = Field(min_length=1, max_length=200)
    home_city: str | None = Field(default=None, max_length=200)
    preferences: TravelPreferences | None = None

    @field_validator("password")
    @classmethod
    def _check_strength(cls, value: str) -> str:
        if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
            raise ValueError("password must contain at least one letter and one digit")
        return value


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int = Field(description="Access token lifetime in seconds.")


class AuthResponse(BaseModel):
    user: UserPublic
    tokens: TokenPair


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _check_strength(cls, value: str) -> str:
        if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
            raise ValueError("password must contain at least one letter and one digit")
        return value


class OAuthLoginRequest(BaseModel):
    """Complete a Google or Apple sign-in.

    The browser performs the sign-in and receives an ID token; that token is
    what gets posted here. The server verifies its signature against the
    provider's public keys before trusting a single claim inside it.
    """

    id_token: str = Field(
        min_length=16,
        max_length=8192,
        description="The signed ID token returned by the provider.",
    )
    nonce: str | None = Field(
        default=None,
        max_length=256,
        description=(
            "The nonce this sign-in was started with. Supply it whenever the "
            "client generated one - it is what prevents a token captured "
            "elsewhere being replayed here."
        ),
    )
    full_name: str | None = Field(
        default=None,
        max_length=200,
        description=(
            "Apple only reveals the user's name on their first authorisation, "
            "and outside the token. Pass it through on that first call."
        ),
    )


class SetPasswordRequest(BaseModel):
    """Add a password to an account that only had social sign-in."""

    new_password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _check_strength(cls, value: str) -> str:
        if not re.search(r"[A-Za-z]", value) or not re.search(r"\d", value):
            raise ValueError("password must contain at least one letter and one digit")
        return value


class AuthMethods(BaseModel):
    """What sign-in options this deployment offers.

    The frontend reads this at load so it can hide a Google or Apple button that
    is not configured - a button that can only fail is worse than no button.
    """

    password: bool = True
    google: bool = False
    apple: bool = False


class LinkedProviders(BaseModel):
    """How the current account can sign in."""

    providers: list[str] = Field(default_factory=list)
    has_password: bool = False
