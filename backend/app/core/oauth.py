"""Verification of Google and Apple ID tokens.

Both providers implement OpenID Connect, so the flow is the same for each: the
browser completes sign-in and hands us a signed JWT (the *ID token*); we verify
that signature against the provider's published keys, then trust the claims
inside it.

What matters here is that the token is verified **fully**, because it is the only
thing standing between a stranger and an account:

* the signature is checked against the provider's current JWKS, fetched live;
* ``iss`` must be the provider we think we are talking to;
* ``aud`` must be *our own* client id - a token minted for a different app is
  cryptographically valid but is not proof that the holder is our user;
* ``exp`` / ``iat`` are enforced by PyJWT;
* ``nonce`` is compared when the caller supplies one, which is what stops a
  token captured elsewhere being replayed into our sign-in.

Never decode these tokens with ``verify_signature=False``. An unverified ID
token is attacker-controlled JSON.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import jwt
from jwt import PyJWKClient

from app.core.config import settings
from app.core.exceptions import AuthenticationError
from app.models.enums import AuthProvider
from app.observability.logging import get_logger

logger = get_logger(__name__)

GOOGLE_ISSUERS = frozenset({"https://accounts.google.com", "accounts.google.com"})
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"

APPLE_ISSUER = "https://appleid.apple.com"
APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"

# Providers rotate signing keys; PyJWKClient caches them and refetches on an
# unknown `kid`. The lifespan keeps a long-running process from pinning a
# retired key set.
_JWKS_CACHE_SECONDS = 3600


@dataclass(slots=True)
class OAuthProfile:
    """The verified identity a provider vouched for."""

    provider: AuthProvider
    subject: str
    email: str | None
    email_verified: bool
    full_name: str | None
    avatar_url: str | None
    raw: dict[str, Any]

    @property
    def is_private_relay(self) -> bool:
        """True for Apple's Hide My Email addresses.

        Worth knowing about: mail to these relays only works while the user keeps
        the app authorised, so they are not a durable contact address.
        """
        return bool(self.email and self.email.endswith("privaterelay.appleid.com"))


_clients: dict[str, PyJWKClient] = {}


def _jwks_client(url: str) -> PyJWKClient:
    if url not in _clients:
        _clients[url] = PyJWKClient(
            url, cache_keys=True, lifespan=_JWKS_CACHE_SECONDS, timeout=10
        )
    return _clients[url]


def _audiences(configured: str | None) -> list[str]:
    """Split a comma-separated client-id setting into accepted audiences.

    Several are allowed because a single project legitimately has one client id
    per platform - web, iOS, Android - and all of them mint tokens for us.
    """
    if not configured:
        return []
    return [part.strip() for part in configured.split(",") if part.strip()]


def _verify(
    token: str,
    *,
    jwks_url: str,
    audiences: list[str],
    issuers: frozenset[str],
    provider: AuthProvider,
    nonce: str | None,
) -> dict[str, Any]:
    """Verify an ID token's signature and claims, returning its payload.

    Raises:
        AuthenticationError: on any verification failure.
    """
    if not audiences:
        raise AuthenticationError(
            f"{provider.value.title()} sign-in is not configured on this server."
        )

    try:
        signing_key = _jwks_client(jwks_url).get_signing_key_from_jwt(token)
    except Exception as exc:
        logger.warning("oauth_jwks_failed", provider=str(provider), error=str(exc))
        raise AuthenticationError(
            f"Could not verify the {provider.value} sign-in token."
        ) from exc

    try:
        payload: dict[str, Any] = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256", "ES256"],
            audience=audiences,
            options={"require": ["exp", "iat", "sub", "aud", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("That sign-in has expired. Please try again.") from exc
    except jwt.InvalidAudienceError as exc:
        raise AuthenticationError(
            "That sign-in token was issued for a different application."
        ) from exc
    except jwt.PyJWTError as exc:
        logger.warning("oauth_token_invalid", provider=str(provider), error=str(exc))
        raise AuthenticationError("That sign-in token is not valid.") from exc

    if payload.get("iss") not in issuers:
        raise AuthenticationError("That sign-in token came from an unexpected issuer.")

    if nonce is not None and payload.get("nonce") != nonce:
        # A mismatch means the token was not minted for this sign-in attempt.
        raise AuthenticationError("That sign-in could not be matched to this request.")

    return payload


async def verify_google_token(token: str, nonce: str | None = None) -> OAuthProfile:
    """Verify a Google ID token and extract the profile it asserts."""
    payload = _verify(
        token,
        jwks_url=GOOGLE_JWKS_URL,
        audiences=_audiences(settings.google_client_id),
        issuers=GOOGLE_ISSUERS,
        provider=AuthProvider.GOOGLE,
        nonce=nonce,
    )
    return OAuthProfile(
        provider=AuthProvider.GOOGLE,
        subject=str(payload["sub"]),
        email=payload.get("email"),
        # Google sends this as a bool, but has historically also sent the string
        # "true"; accept both rather than silently treating it as unverified.
        email_verified=payload.get("email_verified") in (True, "true"),
        full_name=payload.get("name") or _join_names(payload),
        avatar_url=payload.get("picture"),
        raw=payload,
    )


async def verify_apple_token(
    token: str, nonce: str | None = None, full_name: str | None = None
) -> OAuthProfile:
    """Verify an Apple ID token and extract the profile it asserts.

    Apple only sends the user's name on the *first* authorisation, and in the
    form body rather than the token, so ``full_name`` is accepted separately and
    used only to fill a gap.
    """
    payload = _verify(
        token,
        jwks_url=APPLE_JWKS_URL,
        audiences=_audiences(settings.apple_client_id),
        issuers=frozenset({APPLE_ISSUER}),
        provider=AuthProvider.APPLE,
        nonce=nonce,
    )
    return OAuthProfile(
        provider=AuthProvider.APPLE,
        subject=str(payload["sub"]),
        email=payload.get("email"),
        email_verified=payload.get("email_verified") in (True, "true"),
        full_name=full_name or _join_names(payload),
        # Apple never provides a profile picture.
        avatar_url=None,
        raw=payload,
    )


def _join_names(payload: dict[str, Any]) -> str | None:
    parts = [payload.get("given_name"), payload.get("family_name")]
    joined = " ".join(p for p in parts if p)
    return joined or None


async def verify_id_token(
    provider: AuthProvider,
    token: str,
    *,
    nonce: str | None = None,
    full_name: str | None = None,
) -> OAuthProfile:
    """Verify an ID token from whichever provider the caller named."""
    if provider is AuthProvider.GOOGLE:
        return await verify_google_token(token, nonce)
    if provider is AuthProvider.APPLE:
        return await verify_apple_token(token, nonce, full_name)
    raise AuthenticationError(f"{provider} is not a federated sign-in provider.")


def configured_providers() -> list[AuthProvider]:
    """Which federated providers this deployment can actually accept.

    The frontend calls this so it can hide a Google or Apple button that would
    only fail - a dead sign-in button is worse than no button.
    """
    providers: list[AuthProvider] = []
    if _audiences(settings.google_client_id):
        providers.append(AuthProvider.GOOGLE)
    if _audiences(settings.apple_client_id):
        providers.append(AuthProvider.APPLE)
    return providers


def clear_jwks_cache() -> None:
    """Drop cached JWKS clients. Used by tests."""
    _clients.clear()
