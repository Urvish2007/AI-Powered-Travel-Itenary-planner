"""Authentication endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status

from app.core.deps import CurrentUser
from app.core.exceptions import ValidationError
from app.core.oauth import configured_providers
from app.models.enums import AuthProvider
from app.schemas.auth import (
    AuthMethods,
    AuthResponse,
    ChangePasswordRequest,
    LinkedProviders,
    LoginRequest,
    OAuthLoginRequest,
    RefreshRequest,
    RegisterRequest,
    SetPasswordRequest,
    TokenPair,
)
from app.schemas.common import Message
from app.schemas.user import UserPublic
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an account",
)
async def register(payload: RegisterRequest, request: Request) -> AuthResponse:
    user = await auth_service.register_user(payload)
    tokens = await auth_service.issue_tokens(user, request.headers.get("user-agent"))
    return AuthResponse(user=UserPublic.model_validate(user), tokens=tokens)


@router.post(
    "/login",
    response_model=AuthResponse,
    summary="Exchange credentials for tokens",
)
async def login(payload: LoginRequest, request: Request) -> AuthResponse:
    user = await auth_service.authenticate(payload.email, payload.password)
    tokens = await auth_service.issue_tokens(user, request.headers.get("user-agent"))
    return AuthResponse(user=UserPublic.model_validate(user), tokens=tokens)


@router.post(
    "/refresh",
    response_model=TokenPair,
    summary="Rotate a refresh token",
    description=(
        "Refresh tokens are single-use: the presented token is revoked and a new "
        "pair is issued. Replaying a token fails."
    ),
)
async def refresh(payload: RefreshRequest, request: Request) -> TokenPair:
    return await auth_service.rotate_refresh_token(
        payload.refresh_token, request.headers.get("user-agent")
    )


@router.post("/logout", response_model=Message, summary="Revoke a refresh token")
async def logout(payload: RefreshRequest) -> Message:
    await auth_service.revoke_refresh_token(payload.refresh_token)
    return Message(message="Signed out.")


@router.post(
    "/logout-all",
    response_model=Message,
    summary="Revoke every session for the current account",
)
async def logout_all(user: CurrentUser) -> Message:
    revoked = await auth_service.revoke_all_for_user(user.persisted_id)
    return Message(message="All sessions revoked.", details={"revoked": revoked})


@router.post(
    "/change-password",
    response_model=Message,
    summary="Change the password and sign out everywhere",
)
async def change_password(payload: ChangePasswordRequest, user: CurrentUser) -> Message:
    await auth_service.change_password(user, payload.current_password, payload.new_password)
    return Message(message="Password changed. All sessions have been signed out.")


@router.get(
    "/methods",
    response_model=AuthMethods,
    summary="Which sign-in methods this server offers",
    description=(
        "Read at page load so the UI only renders buttons that can actually "
        "work. Google and Apple appear only when their client ids are configured."
    ),
)
async def auth_methods() -> AuthMethods:
    providers = configured_providers()
    return AuthMethods(
        password=True,
        google=AuthProvider.GOOGLE in providers,
        apple=AuthProvider.APPLE in providers,
    )


@router.post(
    "/oauth/{provider}",
    response_model=AuthResponse,
    summary="Sign in with Google or Apple",
    description=(
        "Post the ID token the provider handed the browser. The server verifies "
        "its signature against the provider's published keys, checks the audience "
        "and issuer, then issues this API's own access and refresh tokens.\n\n"
        "A first-time sign-in creates the account and answers **201**; a returning "
        "one answers **200**. Signing in with a provider on an address that "
        "already has a password account links the two instead of creating a "
        "duplicate."
    ),
    responses={
        201: {"description": "A new account was created."},
        401: {"description": "The token failed verification."},
    },
)
async def oauth_login(
    provider: AuthProvider,
    payload: OAuthLoginRequest,
    request: Request,
    response: Response,
) -> AuthResponse:
    if provider is AuthProvider.PASSWORD:
        raise ValidationError(
            "Use /auth/login for password sign-in.",
            details={"provider": provider.value},
        )

    user, created = await auth_service.login_with_provider(
        provider,
        payload.id_token,
        nonce=payload.nonce,
        full_name=payload.full_name,
    )
    if created:
        response.status_code = status.HTTP_201_CREATED

    tokens = await auth_service.issue_tokens(user, request.headers.get("user-agent"))
    return AuthResponse(user=UserPublic.model_validate(user), tokens=tokens)


@router.get(
    "/linked",
    response_model=LinkedProviders,
    summary="How the current account can sign in",
)
async def linked_providers(user: CurrentUser) -> LinkedProviders:
    return LinkedProviders(
        providers=[p.value for p in user.auth_providers],
        has_password=user.has_password,
    )


@router.post(
    "/set-password",
    response_model=Message,
    summary="Add a password to a social-only account",
    description=(
        "For accounts created through Google or Apple that have no password yet. "
        "Returns 409 when one already exists - use change-password for that."
    ),
)
async def set_password(payload: SetPasswordRequest, user: CurrentUser) -> Message:
    await auth_service.set_password(user, payload.new_password)
    return Message(message="Password set. You can now sign in with your email too.")


@router.delete(
    "/linked/{provider}",
    response_model=Message,
    summary="Unlink a social sign-in",
    description=(
        "Refuses with 409 when it is the only way into the account, so a user "
        "cannot accidentally lock themselves out."
    ),
)
async def unlink_provider(provider: AuthProvider, user: CurrentUser) -> Message:
    await auth_service.unlink_provider(user, provider)
    return Message(message=f"{provider.value.title()} sign-in removed.")
