"""User account and preference documents."""

from __future__ import annotations

from datetime import datetime

import pymongo
from beanie import Document
from pydantic import BaseModel, EmailStr, Field
from pymongo import IndexModel

from app.models.base import TimestampedDocument, utcnow
from app.models.enums import (
    AccessibilityNeed,
    AuthProvider,
    BudgetLevel,
    InterestTag,
    TransportMode,
    TravelPace,
    UserRole,
)


class TravelPreferences(BaseModel):
    """Default planning preferences, applied to new trips unless overridden."""

    interests: list[InterestTag] = Field(default_factory=list)
    pace: TravelPace = TravelPace.MODERATE
    budget_level: BudgetLevel = BudgetLevel.MODERATE
    preferred_transport: TransportMode = TransportMode.WALKING
    accessibility_needs: list[AccessibilityNeed] = Field(default_factory=list)
    dietary_restrictions: list[str] = Field(default_factory=list)
    avoid_categories: list[str] = Field(default_factory=list)
    # Local clock, "HH:MM". Constrains the earliest/latest an activity may be scheduled.
    day_start_time: str = Field(default="09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    day_end_time: str = Field(default="21:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    max_daily_walking_km: float = Field(default=8.0, ge=0, le=100)
    language: str = Field(default="en", min_length=2, max_length=8)


class OAuthIdentity(BaseModel):
    """A federated sign-in linked to an account.

    ``subject`` is the provider's stable identifier for the user (the ``sub``
    claim). It is matched on rather than the email, because an email can change
    at the provider while ``sub`` never does.
    """

    provider: AuthProvider
    subject: str = Field(min_length=1, max_length=255)
    email: str | None = None
    linked_at: datetime = Field(default_factory=utcnow)


class User(TimestampedDocument):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    # None for accounts that only ever signed in with Google or Apple. Such an
    # account has no password to verify, so password login is refused outright
    # rather than compared against an empty hash.
    hashed_password: str | None = None
    identities: list[OAuthIdentity] = Field(default_factory=list)
    role: UserRole = UserRole.USER
    is_active: bool = True
    is_verified: bool = False
    preferences: TravelPreferences = Field(default_factory=TravelPreferences)
    home_city: str | None = Field(default=None, max_length=200)
    avatar_url: str | None = Field(default=None, max_length=1000)
    last_login_at: datetime | None = None

    class Settings(TimestampedDocument.Settings):
        name = "users"
        indexes = [
            IndexModel([("email", pymongo.ASCENDING)], unique=True, name="uniq_email"),
            IndexModel([("created_at", pymongo.DESCENDING)], name="users_created_at"),
            # Look up an account by the provider's subject on every federated
            # sign-in. Not unique: the same subject string from two different
            # providers is possible in principle, and the compound lookup is
            # what identifies an identity.
            IndexModel(
                [
                    ("identities.provider", pymongo.ASCENDING),
                    ("identities.subject", pymongo.ASCENDING),
                ],
                name="user_oauth_identity",
            ),
        ]

    def touch_login(self) -> None:
        self.last_login_at = utcnow()

    @property
    def has_password(self) -> bool:
        return bool(self.hashed_password)

    @property
    def auth_providers(self) -> list[AuthProvider]:
        """Every way this account can be signed into."""
        providers = [i.provider for i in self.identities]
        if self.has_password:
            providers.append(AuthProvider.PASSWORD)
        return providers

    def identity_for(self, provider: AuthProvider) -> OAuthIdentity | None:
        return next((i for i in self.identities if i.provider is provider), None)

    def link_identity(
        self, provider: AuthProvider, subject: str, email: str | None
    ) -> bool:
        """Attach a federated identity. Returns True when something changed."""
        existing = self.identity_for(provider)
        if existing is not None:
            if existing.subject == subject:
                return False
            # The provider reports a different subject for the same provider,
            # which means this is a different provider account. Replace it.
            self.identities = [i for i in self.identities if i.provider is not provider]
        self.identities.append(
            OAuthIdentity(provider=provider, subject=subject, email=email)
        )
        return True


class RefreshToken(Document):
    """Server-side record of issued refresh tokens, so they can be revoked.

    ``expires_at`` carries a TTL index: MongoDB removes rows automatically once the
    token is past its lifetime, which keeps the collection self-pruning.
    """

    jti: str
    user_id: str
    issued_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    revoked: bool = False
    user_agent: str | None = None

    class Settings:
        name = "refresh_tokens"
        indexes = [
            IndexModel([("jti", pymongo.ASCENDING)], unique=True, name="uniq_jti"),
            IndexModel([("user_id", pymongo.ASCENDING)], name="refresh_user"),
            IndexModel(
                [("expires_at", pymongo.ASCENDING)],
                expireAfterSeconds=0,
                name="refresh_ttl",
            ),
        ]
