"""Reusable FastAPI dependencies: authentication and authorisation."""

from __future__ import annotations

from typing import Annotated

import jwt
from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.exceptions import AuthenticationError, PermissionDeniedError
from app.core.security import decode_token
from app.models.enums import UserRole
from app.models.user import User
from app.services import auth_service

# auto_error=False so a missing header raises this application's error shape
# rather than Starlette's bare 403.
bearer_scheme = HTTPBearer(auto_error=False, description="JWT access token")


async def get_current_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User:
    """Resolve the caller from the bearer token.

    Raises:
        AuthenticationError: when the header is absent, malformed, expired, or
            refers to a user who no longer exists or has been disabled.
    """
    if credentials is None or not credentials.credentials:
        raise AuthenticationError("An Authorization: Bearer <token> header is required.")

    try:
        payload = decode_token(credentials.credentials, "access")
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("The access token has expired.") from exc
    except jwt.PyJWTError as exc:
        raise AuthenticationError("The access token is invalid.") from exc

    subject = payload.get("sub")
    if not subject:
        raise AuthenticationError("The access token is missing a subject.")

    try:
        user = await auth_service.get_user_by_id(subject)
    except Exception as exc:
        raise AuthenticationError("The account for this token no longer exists.") from exc

    if not user.is_active:
        raise AuthenticationError("This account has been disabled.")
    return user


async def get_optional_user(
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
) -> User | None:
    """Resolve the caller when a token is present, else None.

    Used by endpoints that serve both public and personalised responses.
    """
    if credentials is None or not credentials.credentials:
        return None
    try:
        return await get_current_user(credentials)
    except AuthenticationError:
        return None


async def require_admin(
    user: Annotated[User, Depends(get_current_user)],
) -> User:
    """Restrict an endpoint to administrators.

    Raises:
        PermissionDeniedError: when the caller is not an admin.
    """
    if user.role is not UserRole.ADMIN:
        raise PermissionDeniedError("This endpoint requires administrator access.")
    return user


def client_ip(request: Request) -> str:
    """Best-effort client address for rate limiting and audit logs.

    ``X-Forwarded-For`` is trusted only for its first entry, and only because the
    service is expected to sit behind a proxy that sets it. Without such a proxy
    this header is client-controlled and must not be used for anything
    security-critical.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


CurrentUser = Annotated[User, Depends(get_current_user)]
OptionalUser = Annotated[User | None, Depends(get_optional_user)]
AdminUser = Annotated[User, Depends(require_admin)]
