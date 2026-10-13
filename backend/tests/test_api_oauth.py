"""Integration tests for Google and Apple sign-in.

These do not talk to Google or Apple. Instead the suite generates its own RSA
keypair, signs ID tokens with it, and points the verifier's key lookup at the
matching public key. That keeps the tests offline while still exercising the
*real* verification path — signature, audience, issuer, expiry and nonce are all
genuinely checked, so a regression that weakened any of them would fail here.

An "accept anything" mock would pass regardless, which is precisely what makes it
worthless for an authentication boundary.
"""

from __future__ import annotations

import time
import uuid
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from httpx import AsyncClient

from app.core import oauth as oauth_module
from app.models.enums import AuthProvider

pytestmark = pytest.mark.asyncio

AUTH = "/api/v1/auth"

GOOGLE_CLIENT_ID = "test-google-client.apps.googleusercontent.com"
APPLE_CLIENT_ID = "com.example.itinerary.service"

_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _StubSigningKey:
    def __init__(self, key: Any) -> None:
        self.key = key


class _StubJWKSClient:
    """Stands in for PyJWKClient, returning a fixed public key."""

    def __init__(self, key: Any) -> None:
        self._key = key

    def get_signing_key_from_jwt(self, token: str) -> _StubSigningKey:
        return _StubSigningKey(self._key)


@pytest.fixture(autouse=True)
def _configure_providers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the verifier at the test keypair and configure both client ids."""
    monkeypatch.setattr(
        oauth_module.settings, "google_client_id", GOOGLE_CLIENT_ID, raising=False
    )
    monkeypatch.setattr(
        oauth_module.settings, "apple_client_id", APPLE_CLIENT_ID, raising=False
    )
    monkeypatch.setattr(
        oauth_module,
        "_jwks_client",
        lambda url: _StubJWKSClient(_PRIVATE_KEY.public_key()),
    )
    oauth_module.clear_jwks_cache()


def make_token(
    *,
    provider: AuthProvider = AuthProvider.GOOGLE,
    subject: str | None = None,
    email: str | None = None,
    email_verified: bool = True,
    name: str | None = "Federated User",
    audience: str | None = None,
    issuer: str | None = None,
    nonce: str | None = None,
    expires_in: int = 600,
    key: Any = None,
    picture: str | None = None,
) -> str:
    """Mint an ID token shaped like the provider's."""
    now = int(time.time())
    if provider is AuthProvider.GOOGLE:
        audience = audience or GOOGLE_CLIENT_ID
        issuer = issuer or "https://accounts.google.com"
    else:
        audience = audience or APPLE_CLIENT_ID
        issuer = issuer or "https://appleid.apple.com"

    payload: dict[str, Any] = {
        "iss": issuer,
        "aud": audience,
        "sub": subject or f"sub-{uuid.uuid4().hex[:12]}",
        "iat": now,
        "exp": now + expires_in,
        "email_verified": email_verified,
    }
    if email is not None:
        payload["email"] = email
    if name is not None:
        payload["name"] = name
    if nonce is not None:
        payload["nonce"] = nonce
    if picture is not None:
        payload["picture"] = picture

    return jwt.encode(payload, key or _PRIVATE_KEY, algorithm="RS256")


class TestAuthMethods:
    async def test_reports_configured_providers(self, client: AsyncClient) -> None:
        response = await client.get(f"{AUTH}/methods")
        assert response.status_code == 200

        body = response.json()
        assert body == {"password": True, "google": True, "apple": True}

    async def test_hides_providers_that_are_not_configured(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A button that can only fail is worse than no button."""
        monkeypatch.setattr(oauth_module.settings, "google_client_id", None, raising=False)
        monkeypatch.setattr(oauth_module.settings, "apple_client_id", "", raising=False)

        body = (await client.get(f"{AUTH}/methods")).json()
        assert body["google"] is False
        assert body["apple"] is False
        assert body["password"] is True


class TestGoogleSignIn:
    async def test_first_sign_in_creates_the_account(self, client: AsyncClient) -> None:
        email = f"g-{uuid.uuid4().hex[:8]}@example.com"
        response = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": make_token(email=email, name="Ada Lovelace")},
        )
        assert response.status_code == 201, response.text

        body = response.json()
        assert body["user"]["email"] == email
        assert body["user"]["full_name"] == "Ada Lovelace"
        assert body["tokens"]["access_token"]

    async def test_returning_sign_in_reuses_the_account(self, client: AsyncClient) -> None:
        email = f"g-{uuid.uuid4().hex[:8]}@example.com"
        subject = "stable-google-subject"

        first = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": make_token(email=email, subject=subject)},
        )
        assert first.status_code == 201

        second = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": make_token(email=email, subject=subject)},
        )
        assert second.status_code == 200, "a returning sign-in must not be a create"
        assert second.json()["user"]["id"] == first.json()["user"]["id"]

    async def test_matched_by_subject_even_when_the_email_changed(
        self, client: AsyncClient
    ) -> None:
        """``sub`` is stable; an email is not. The link must survive a change."""
        subject = "subject-that-outlives-the-email"
        first = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": make_token(email="before@example.com", subject=subject)},
        )
        assert first.status_code == 201

        second = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": make_token(email="after@example.com", subject=subject)},
        )
        assert second.status_code == 200
        assert second.json()["user"]["id"] == first.json()["user"]["id"]

    async def test_issued_tokens_work_on_a_protected_endpoint(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": make_token(email=f"g-{uuid.uuid4().hex[:8]}@example.com")},
        )
        access = response.json()["tokens"]["access_token"]

        me = await client.get(
            "/api/v1/users/me", headers={"Authorization": f"Bearer {access}"}
        )
        assert me.status_code == 200

    async def test_avatar_is_stored(self, client: AsyncClient) -> None:
        response = await client.post(
            f"{AUTH}/oauth/google",
            json={
                "id_token": make_token(
                    email=f"g-{uuid.uuid4().hex[:8]}@example.com",
                    picture="https://example.com/avatar.png",
                )
            },
        )
        access = response.json()["tokens"]["access_token"]
        me = await client.get(
            "/api/v1/users/me", headers={"Authorization": f"Bearer {access}"}
        )
        assert me.json()["avatar_url"] == "https://example.com/avatar.png"


class TestTokenVerification:
    """Every one of these must be rejected, or the endpoint is an open door."""

    async def test_rejects_a_token_signed_by_someone_else(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": make_token(email="x@example.com", key=_OTHER_KEY)},
        )
        assert response.status_code == 401

    async def test_rejects_a_tampered_token(self, client: AsyncClient) -> None:
        token = make_token(email="x@example.com")
        head, payload, signature = token.split(".")
        response = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": f"{head}.{payload}.{signature[:-6]}AAAAAA"},
        )
        assert response.status_code == 401

    async def test_rejects_a_token_for_another_application(
        self, client: AsyncClient
    ) -> None:
        """A valid Google token minted for a different app is not proof of our user."""
        response = await client.post(
            f"{AUTH}/oauth/google",
            json={
                "id_token": make_token(
                    email="x@example.com", audience="someone-elses-client-id"
                )
            },
        )
        assert response.status_code == 401

    async def test_rejects_an_expired_token(self, client: AsyncClient) -> None:
        response = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": make_token(email="x@example.com", expires_in=-60)},
        )
        assert response.status_code == 401

    async def test_rejects_an_unexpected_issuer(self, client: AsyncClient) -> None:
        response = await client.post(
            f"{AUTH}/oauth/google",
            json={
                "id_token": make_token(
                    email="x@example.com", issuer="https://evil.example.com"
                )
            },
        )
        assert response.status_code == 401

    async def test_rejects_a_mismatched_nonce(self, client: AsyncClient) -> None:
        """Nonce binding is what stops a token captured elsewhere being replayed."""
        response = await client.post(
            f"{AUTH}/oauth/google",
            json={
                "id_token": make_token(email="x@example.com", nonce="the-real-nonce"),
                "nonce": "a-different-nonce",
            },
        )
        assert response.status_code == 401

    async def test_accepts_a_matching_nonce(self, client: AsyncClient) -> None:
        response = await client.post(
            f"{AUTH}/oauth/google",
            json={
                "id_token": make_token(
                    email=f"g-{uuid.uuid4().hex[:8]}@example.com", nonce="matching"
                ),
                "nonce": "matching",
            },
        )
        assert response.status_code == 201

    async def test_rejects_garbage(self, client: AsyncClient) -> None:
        response = await client.post(
            f"{AUTH}/oauth/google", json={"id_token": "not-even-a-jwt-at-all"}
        )
        assert response.status_code == 401

    async def test_refuses_when_the_provider_is_not_configured(
        self, client: AsyncClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(oauth_module.settings, "google_client_id", None, raising=False)
        response = await client.post(
            f"{AUTH}/oauth/google", json={"id_token": make_token(email="x@example.com")}
        )
        assert response.status_code == 401
        assert "not configured" in response.json()["error"]["message"]

    async def test_password_provider_is_not_a_federated_route(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            f"{AUTH}/oauth/password", json={"id_token": make_token(email="x@e.com")}
        )
        assert response.status_code == 422


class TestAppleSignIn:
    async def test_creates_an_account(self, client: AsyncClient) -> None:
        email = f"a-{uuid.uuid4().hex[:8]}@example.com"
        response = await client.post(
            f"{AUTH}/oauth/apple",
            json={
                "id_token": make_token(provider=AuthProvider.APPLE, email=email, name=None),
                "full_name": "Grace Hopper",
            },
        )
        assert response.status_code == 201, response.text
        # Apple withholds the name from the token, so the body value is used.
        assert response.json()["user"]["full_name"] == "Grace Hopper"

    async def test_private_relay_address_is_accepted(self, client: AsyncClient) -> None:
        """Hide My Email is the default for many users; it must work."""
        relay = f"{uuid.uuid4().hex[:16]}@privaterelay.appleid.com"
        response = await client.post(
            f"{AUTH}/oauth/apple",
            json={
                "id_token": make_token(provider=AuthProvider.APPLE, email=relay),
            },
        )
        assert response.status_code == 201
        assert response.json()["user"]["email"] == relay

    async def test_apple_token_is_rejected_on_the_google_route(
        self, client: AsyncClient
    ) -> None:
        """Audience checking must be per-provider, not shared."""
        response = await client.post(
            f"{AUTH}/oauth/google",
            json={
                "id_token": make_token(provider=AuthProvider.APPLE, email="x@example.com")
            },
        )
        assert response.status_code == 401

    async def test_falls_back_to_the_email_local_part_for_a_name(
        self, client: AsyncClient
    ) -> None:
        email = f"nameless-{uuid.uuid4().hex[:6]}@example.com"
        response = await client.post(
            f"{AUTH}/oauth/apple",
            json={
                "id_token": make_token(provider=AuthProvider.APPLE, email=email, name=None)
            },
        )
        assert response.status_code == 201
        assert response.json()["user"]["full_name"] == email.split("@")[0]


class TestAccountLinking:
    async def test_links_to_an_existing_password_account(self, client: AsyncClient) -> None:
        """Otherwise the user gets a second, empty account and loses their trips."""
        email = f"link-{uuid.uuid4().hex[:8]}@example.com"
        registered = await client.post(
            f"{AUTH}/register",
            json={
                "email": email,
                "password": "originalPass123",
                "full_name": "Original Name",
            },
        )
        assert registered.status_code == 201
        user_id = registered.json()["user"]["id"]

        federated = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": make_token(email=email, name="Google Name")},
        )
        assert federated.status_code == 200, "linking must not create a second account"
        assert federated.json()["user"]["id"] == user_id
        # A name the user already set is not overwritten by the provider's.
        assert federated.json()["user"]["full_name"] == "Original Name"

    async def test_does_not_link_on_an_unverified_email(self, client: AsyncClient) -> None:
        """Linking on an unverified email would let anyone claim the account."""
        email = f"unver-{uuid.uuid4().hex[:8]}@example.com"
        await client.post(
            f"{AUTH}/register",
            json={
                "email": email,
                "password": "originalPass123",
                "full_name": "Rightful Owner",
            },
        )

        response = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": make_token(email=email, email_verified=False)},
        )
        # It cannot link, and it cannot create either - the email is taken.
        assert response.status_code in (401, 409)

    async def test_linked_endpoint_lists_both_methods(self, client: AsyncClient) -> None:
        email = f"both-{uuid.uuid4().hex[:8]}@example.com"
        await client.post(
            f"{AUTH}/register",
            json={"email": email, "password": "originalPass123", "full_name": "Both"},
        )
        federated = await client.post(
            f"{AUTH}/oauth/google", json={"id_token": make_token(email=email)}
        )
        auth = {"Authorization": f"Bearer {federated.json()['tokens']['access_token']}"}

        response = await client.get(f"{AUTH}/linked", headers=auth)
        assert response.status_code == 200

        body = response.json()
        assert body["has_password"] is True
        assert set(body["providers"]) == {"google", "password"}

    async def test_unlink_is_refused_when_it_is_the_only_method(
        self, client: AsyncClient
    ) -> None:
        """Nobody should be able to lock themselves out in one click."""
        response = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": make_token(email=f"solo-{uuid.uuid4().hex[:8]}@e.com")},
        )
        auth = {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}

        unlink = await client.delete(f"{AUTH}/linked/google", headers=auth)
        assert unlink.status_code == 409

    async def test_unlink_succeeds_once_a_password_exists(
        self, client: AsyncClient
    ) -> None:
        response = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": make_token(email=f"upg-{uuid.uuid4().hex[:8]}@e.com")},
        )
        auth = {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}

        assert (
            await client.post(
                f"{AUTH}/set-password",
                headers=auth,
                json={"new_password": "brandNewPass123"},
            )
        ).status_code == 200

        assert (
            await client.delete(f"{AUTH}/linked/google", headers=auth)
        ).status_code == 200

    async def test_unlink_a_provider_that_is_not_linked(self, client: AsyncClient) -> None:
        response = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": make_token(email=f"nl-{uuid.uuid4().hex[:8]}@e.com")},
        )
        auth = {"Authorization": f"Bearer {response.json()['tokens']['access_token']}"}

        assert (
            await client.delete(f"{AUTH}/linked/apple", headers=auth)
        ).status_code == 404


class TestPasswordInteraction:
    async def test_social_only_account_cannot_sign_in_with_a_password(
        self, client: AsyncClient
    ) -> None:
        email = f"nopw-{uuid.uuid4().hex[:8]}@example.com"
        await client.post(
            f"{AUTH}/oauth/google", json={"id_token": make_token(email=email)}
        )

        response = await client.post(
            f"{AUTH}/login", json={"email": email, "password": "anythingAtAll1"}
        )
        assert response.status_code == 401
        # The message must say what to do instead, not just "wrong password".
        assert "google" in response.json()["error"]["message"].lower()

    async def test_set_password_then_sign_in_with_it(self, client: AsyncClient) -> None:
        email = f"setpw-{uuid.uuid4().hex[:8]}@example.com"
        federated = await client.post(
            f"{AUTH}/oauth/google", json={"id_token": make_token(email=email)}
        )
        auth = {"Authorization": f"Bearer {federated.json()['tokens']['access_token']}"}

        assert (
            await client.post(
                f"{AUTH}/set-password",
                headers=auth,
                json={"new_password": "freshPassword123"},
            )
        ).status_code == 200

        login = await client.post(
            f"{AUTH}/login", json={"email": email, "password": "freshPassword123"}
        )
        assert login.status_code == 200

    async def test_set_password_is_refused_when_one_exists(
        self, client: AsyncClient
    ) -> None:
        registered = await client.post(
            f"{AUTH}/register",
            json={
                "email": f"has-{uuid.uuid4().hex[:8]}@example.com",
                "password": "originalPass123",
                "full_name": "Has Password",
            },
        )
        auth = {"Authorization": f"Bearer {registered.json()['tokens']['access_token']}"}

        response = await client.post(
            f"{AUTH}/set-password", headers=auth, json={"new_password": "another123"}
        )
        assert response.status_code == 409

    async def test_set_password_rejects_a_weak_one(self, client: AsyncClient) -> None:
        federated = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": make_token(email=f"weak-{uuid.uuid4().hex[:8]}@e.com")},
        )
        auth = {"Authorization": f"Bearer {federated.json()['tokens']['access_token']}"}

        response = await client.post(
            f"{AUTH}/set-password", headers=auth, json={"new_password": "short"}
        )
        assert response.status_code == 422

    async def test_disabled_account_cannot_sign_in_with_a_provider(
        self, client: AsyncClient
    ) -> None:
        email = f"dis-{uuid.uuid4().hex[:8]}@example.com"
        subject = "disabled-subject"
        federated = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": make_token(email=email, subject=subject)},
        )
        auth = {"Authorization": f"Bearer {federated.json()['tokens']['access_token']}"}
        assert (await client.delete("/api/v1/users/me", headers=auth)).status_code == 200

        response = await client.post(
            f"{AUTH}/oauth/google",
            json={"id_token": make_token(email=email, subject=subject)},
        )
        assert response.status_code == 403
