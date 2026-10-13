"""Registration, login, token rotation and password changes."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from beanie import PydanticObjectId

from app.core.config import settings
from app.core.exceptions import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
)
from app.core.oauth import OAuthProfile, verify_id_token
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    needs_rehash,
    verify_password,
)
from app.models.enums import AuthProvider
from app.models.user import RefreshToken, TravelPreferences, User
from app.observability.logging import get_logger
from app.schemas.auth import RegisterRequest, TokenPair

logger = get_logger(__name__)


def normalize_email(email: str) -> str:
    """Lower-case the address so lookups are case-insensitive.

    The unique index is on the stored value, so normalising on the way in is what
    actually prevents ``Alice@x.com`` and ``alice@x.com`` becoming two accounts.
    """
    return email.strip().lower()


async def register_user(payload: RegisterRequest) -> User:
    """Create a new account.

    Raises:
        ConflictError: when the email is already registered.
    """
    email = normalize_email(payload.email)
    if await User.find_one(User.email == email) is not None:
        raise ConflictError("An account with that email already exists.")

    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        hashed_password=hash_password(payload.password),
        home_city=payload.home_city,
        preferences=payload.preferences or TravelPreferences(),
    )
    await user.insert()
    logger.info("user_registered", user_id=str(user.id), email=email)
    return user


async def authenticate(email: str, password: str) -> User:
    """Verify credentials and return the user.

    Raises:
        AuthenticationError: on unknown email, wrong password, or disabled account.
    """
    user = await User.find_one(User.email == normalize_email(email))
    if user is None:
        # Spend the same work as a real verification so that a missing account
        # and a wrong password take indistinguishable time.
        hash_password(password)
        raise AuthenticationError("Incorrect email or password.")

    if not user.has_password:
        # An account created through Google or Apple has no password to check.
        # Say which provider to use rather than a bare "incorrect password",
        # which would be both wrong and impossible to act on.
        providers = ", ".join(
            p.value for p in user.auth_providers if p is not AuthProvider.PASSWORD
        )
        raise AuthenticationError(
            f"This account signs in with {providers or 'a social provider'}. "
            "Use that button instead, or set a password first."
        )

    if not verify_password(password, user.hashed_password or ""):
        raise AuthenticationError("Incorrect email or password.")

    if not user.is_active:
        raise AuthenticationError("This account has been disabled.")

    # Opportunistically upgrade hashes when Argon2 parameters change.
    if needs_rehash(user.hashed_password):
        user.hashed_password = hash_password(password)

    user.touch_login()
    await user.save()
    return user


async def issue_tokens(user: User, user_agent: str | None = None) -> TokenPair:
    """Mint an access/refresh pair and record the refresh token server-side."""
    access = create_access_token(str(user.id))
    refresh = create_refresh_token(str(user.id))

    payload = decode_token(refresh, "refresh")
    await RefreshToken(
        jti=payload["jti"],
        user_id=str(user.id),
        expires_at=datetime.fromtimestamp(payload["exp"], tz=UTC),
        user_agent=user_agent,
    ).insert()

    return TokenPair(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_expire_minutes * 60,
    )


async def rotate_refresh_token(token: str, user_agent: str | None = None) -> TokenPair:
    """Exchange a refresh token for a new pair, revoking the old one.

    Rotation is one-use: presenting the same refresh token twice fails, which is
    what makes a stolen token detectable rather than indefinitely usable.

    Raises:
        AuthenticationError: when the token is invalid, expired, or already used.
    """
    try:
        payload = decode_token(token, "refresh")
    except jwt.PyJWTError as exc:
        raise AuthenticationError("The refresh token is invalid or has expired.") from exc

    record = await RefreshToken.find_one(RefreshToken.jti == payload["jti"])
    if record is None or record.revoked:
        raise AuthenticationError("This refresh token is no longer valid.")

    user = await User.get(PydanticObjectId(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthenticationError("The account for this token is unavailable.")

    record.revoked = True
    await record.save()

    return await issue_tokens(user, user_agent)


async def revoke_refresh_token(token: str) -> None:
    """Invalidate a refresh token. Unknown or malformed tokens are ignored.

    Logout should succeed even when the presented token was already useless -
    the caller's intent (end this session) is satisfied either way.
    """
    try:
        payload = decode_token(token, "refresh")
    except jwt.PyJWTError:
        return

    record = await RefreshToken.find_one(RefreshToken.jti == payload["jti"])
    if record is not None and not record.revoked:
        record.revoked = True
        await record.save()


async def revoke_all_for_user(user_id: PydanticObjectId | str) -> int:
    """Revoke every outstanding refresh token for a user. Returns the count."""
    result = await RefreshToken.find(
        RefreshToken.user_id == str(user_id),
        RefreshToken.revoked == False,  # noqa: E712
    ).update({"$set": {"revoked": True}})
    return getattr(result, "modified_count", 0)


async def change_password(user: User, current: str, new: str) -> None:
    """Change a password and invalidate every existing session.

    Raises:
        AuthenticationError: when the current password does not match.
    """
    if not verify_password(current, user.hashed_password):
        raise AuthenticationError("The current password is incorrect.")

    user.hashed_password = hash_password(new)
    await user.save()
    await revoke_all_for_user(user.id)
    logger.info("password_changed", user_id=str(user.id))


async def get_user_by_id(user_id: str | PydanticObjectId) -> User:
    """Fetch a user or raise.

    Raises:
        NotFoundError: when no such user exists.
    """
    try:
        oid = PydanticObjectId(str(user_id))
    except Exception as exc:
        raise NotFoundError("User not found.") from exc

    user = await User.get(oid)
    if user is None:
        raise NotFoundError("User not found.")
    return user


async def purge_expired_tokens() -> int:
    """Delete refresh tokens past their expiry.

    MongoDB's TTL monitor does this on its own roughly once a minute; this exists
    so a worker or test can force the cleanup deterministically.
    """
    result = await RefreshToken.find(RefreshToken.expires_at < datetime.now(UTC)).delete()
    return getattr(result, "deleted_count", 0)


def access_token_lifetime() -> timedelta:
    return timedelta(minutes=settings.access_token_expire_minutes)


async def login_with_provider(
    provider: AuthProvider,
    id_token: str,
    *,
    nonce: str | None = None,
    full_name: str | None = None,
) -> tuple[User, bool]:
    """Sign in (or sign up) with a verified Google or Apple ID token.

    Returns:
        ``(user, created)`` - ``created`` is True when this call registered a
        brand-new account, which lets the API answer 201 rather than 200.

    Raises:
        AuthenticationError: when the token fails verification.
        PermissionDeniedError: when the matched account is disabled.

    Account resolution happens in three steps, in this order:

    1. **By provider subject.** The provider's ``sub`` is the only identifier
       that never changes, so an existing link wins even if the email moved.
    2. **By verified email.** A Google sign-in on an address that already has a
       password account links to it, rather than creating a second account the
       user cannot see their trips in. This is only safe because the provider
       asserted the email is verified - linking on an *unverified* email would
       let anyone claim an account by signing up at the provider with its
       address.
    3. **Create.** Nothing matched, so this is a new traveller.
    """
    profile: OAuthProfile = await verify_id_token(
        provider, id_token, nonce=nonce, full_name=full_name
    )

    user = await User.find_one(
        {
            "identities": {
                "$elemMatch": {
                    "provider": provider.value,
                    "subject": profile.subject,
                }
            }
        }
    )
    created = False

    if user is None and profile.email and profile.email_verified:
        user = await User.find_one(User.email == normalize_email(profile.email))
        if user is not None:
            user.link_identity(provider, profile.subject, profile.email)
            logger.info(
                "oauth_identity_linked",
                provider=str(provider),
                user_id=str(user.id),
            )

    if user is None:
        email = normalize_email(profile.email) if profile.email else None
        if not email:
            # Apple can withhold the email if the user revoked the scope. Without
            # one there is no way to build an account, and no way to recover it.
            raise AuthenticationError(
                "That provider did not share an email address, which is required "
                "to create an account. Re-authorise it and allow email sharing."
            )

        # The address is taken but we could not link to it, which means the
        # provider did not vouch for the email. Creating anyway would trip the
        # unique index and surface as a 500, so refuse with an explanation the
        # user can act on.
        if await User.find_one(User.email == email) is not None:
            raise ConflictError(
                "An account already exists for that email address, and "
                f"{provider.value} did not confirm the address belongs to you. "
                "Sign in with your password, then link "
                f"{provider.value.title()} from your account settings."
            )

        user = User(
            email=email,
            full_name=(profile.full_name or email.split("@")[0]).strip()[:200],
            hashed_password=None,
            avatar_url=profile.avatar_url,
            # The provider already proved control of the address, so there is no
            # separate verification email to send.
            is_verified=profile.email_verified,
        )
        user.link_identity(provider, profile.subject, profile.email)
        await user.insert()
        created = True
        logger.info(
            "oauth_user_registered",
            provider=str(provider),
            user_id=str(user.id),
            private_relay=profile.is_private_relay,
        )

    if not user.is_active:
        raise PermissionDeniedError("This account has been disabled.")

    # Keep the profile picture fresh, but never overwrite a name the user edited
    # with the provider's version.
    if profile.avatar_url and profile.avatar_url != user.avatar_url:
        user.avatar_url = profile.avatar_url

    user.touch_login()
    await user.save()
    return user, created


async def set_password(user: User, new_password: str) -> None:
    """Give an OAuth-only account a password for the first time.

    Distinct from ``change_password``: there is no current password to verify,
    so this is only reachable for an account that genuinely has none.

    Raises:
        ConflictError: when the account already has a password.
    """
    if user.has_password:
        raise ConflictError(
            "This account already has a password. Use change-password instead."
        )
    user.hashed_password = hash_password(new_password)
    await user.save()
    logger.info("password_set", user_id=str(user.id))


async def unlink_provider(user: User, provider: AuthProvider) -> None:
    """Remove a federated sign-in from an account.

    Raises:
        ConflictError: when removing it would leave no way to sign in.
    """
    if user.identity_for(provider) is None:
        raise NotFoundError(f"This account is not linked to {provider.value}.")

    remaining = [p for p in user.auth_providers if p is not provider]
    if not remaining:
        raise ConflictError(
            f"{provider.value.title()} is the only way to sign into this account. "
            "Set a password first, then unlink it."
        )

    user.identities = [i for i in user.identities if i.provider is not provider]
    await user.save()
    logger.info("oauth_identity_unlinked", provider=str(provider), user_id=str(user.id))
