"""Integration tests for authentication and user endpoints."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

AUTH = "/api/v1/auth"
USERS = "/api/v1/users"


def _registration(**overrides: Any) -> dict[str, Any]:
    payload = {
        "email": f"user-{uuid.uuid4().hex[:8]}@example.com",
        "password": "validPassword123",
        "full_name": "Registered User",
    }
    payload.update(overrides)
    return payload


class TestRegistration:
    async def test_creates_an_account_and_returns_tokens(self, client: AsyncClient) -> None:
        response = await client.post(f"{AUTH}/register", json=_registration())
        assert response.status_code == 201

        body = response.json()
        assert body["tokens"]["access_token"]
        assert body["tokens"]["refresh_token"]
        assert body["tokens"]["token_type"] == "bearer"
        assert body["user"]["role"] == "user"

    async def test_never_returns_the_password_hash(self, client: AsyncClient) -> None:
        response = await client.post(f"{AUTH}/register", json=_registration())
        assert "hashed_password" not in response.text
        assert "hashed_password" not in response.json()["user"]

    async def test_duplicate_email_conflicts(self, client: AsyncClient) -> None:
        payload = _registration()
        assert (await client.post(f"{AUTH}/register", json=payload)).status_code == 201
        second = await client.post(f"{AUTH}/register", json=payload)
        assert second.status_code == 409
        assert second.json()["error"]["code"] == "conflict"

    async def test_email_is_case_insensitive(self, client: AsyncClient) -> None:
        """Alice@x.com and alice@x.com must not become two accounts."""
        payload = _registration(email="MixedCase@Example.com")
        assert (await client.post(f"{AUTH}/register", json=payload)).status_code == 201

        duplicate = _registration(email="mixedcase@example.com")
        assert (await client.post(f"{AUTH}/register", json=duplicate)).status_code == 409

    @pytest.mark.parametrize(
        "password",
        ["short1", "nodigitshere", "1234567890", "       1a"],
    )
    async def test_rejects_weak_passwords(self, client: AsyncClient, password: str) -> None:
        response = await client.post(
            f"{AUTH}/register", json=_registration(password=password)
        )
        assert response.status_code == 422

    async def test_rejects_an_invalid_email(self, client: AsyncClient) -> None:
        response = await client.post(
            f"{AUTH}/register", json=_registration(email="not-an-email")
        )
        assert response.status_code == 422


class TestLogin:
    async def test_valid_credentials(self, client: AsyncClient) -> None:
        payload = _registration()
        await client.post(f"{AUTH}/register", json=payload)

        response = await client.post(
            f"{AUTH}/login",
            json={"email": payload["email"], "password": payload["password"]},
        )
        assert response.status_code == 200
        assert response.json()["tokens"]["access_token"]

    async def test_wrong_password_is_unauthorised(self, client: AsyncClient) -> None:
        payload = _registration()
        await client.post(f"{AUTH}/register", json=payload)

        response = await client.post(
            f"{AUTH}/login", json={"email": payload["email"], "password": "wrongPass123"}
        )
        assert response.status_code == 401

    async def test_unknown_account_is_unauthorised(self, client: AsyncClient) -> None:
        response = await client.post(
            f"{AUTH}/login",
            json={"email": "nobody@example.com", "password": "somePassword123"},
        )
        assert response.status_code == 401

    async def test_error_does_not_reveal_whether_the_account_exists(
        self, client: AsyncClient
    ) -> None:
        """Both failures must return the same message, or the endpoint enumerates users."""
        payload = _registration()
        await client.post(f"{AUTH}/register", json=payload)

        wrong_password = await client.post(
            f"{AUTH}/login", json={"email": payload["email"], "password": "wrongPass123"}
        )
        unknown_user = await client.post(
            f"{AUTH}/login",
            json={"email": "nobody@example.com", "password": "wrongPass123"},
        )
        assert (
            wrong_password.json()["error"]["message"]
            == unknown_user.json()["error"]["message"]
        )


class TestRefreshRotation:
    async def test_returns_a_new_pair(self, client: AsyncClient) -> None:
        registered = await client.post(f"{AUTH}/register", json=_registration())
        refresh = registered.json()["tokens"]["refresh_token"]

        response = await client.post(f"{AUTH}/refresh", json={"refresh_token": refresh})
        assert response.status_code == 200
        assert response.json()["refresh_token"] != refresh

    async def test_old_token_cannot_be_replayed(self, client: AsyncClient) -> None:
        """Single-use rotation is what makes a stolen refresh token detectable."""
        registered = await client.post(f"{AUTH}/register", json=_registration())
        refresh = registered.json()["tokens"]["refresh_token"]

        assert (
            await client.post(f"{AUTH}/refresh", json={"refresh_token": refresh})
        ).status_code == 200
        replayed = await client.post(f"{AUTH}/refresh", json={"refresh_token": refresh})
        assert replayed.status_code == 401

    async def test_an_access_token_is_not_accepted_as_a_refresh_token(
        self, client: AsyncClient
    ) -> None:
        registered = await client.post(f"{AUTH}/register", json=_registration())
        access = registered.json()["tokens"]["access_token"]

        response = await client.post(f"{AUTH}/refresh", json={"refresh_token": access})
        assert response.status_code == 401

    async def test_logout_revokes_the_token(self, client: AsyncClient) -> None:
        registered = await client.post(f"{AUTH}/register", json=_registration())
        refresh = registered.json()["tokens"]["refresh_token"]

        assert (
            await client.post(f"{AUTH}/logout", json={"refresh_token": refresh})
        ).status_code == 200
        assert (
            await client.post(f"{AUTH}/refresh", json={"refresh_token": refresh})
        ).status_code == 401

    async def test_logout_of_an_unknown_token_still_succeeds(
        self, client: AsyncClient
    ) -> None:
        """Ending a session the caller no longer has is not an error."""
        response = await client.post(
            f"{AUTH}/logout", json={"refresh_token": "garbage-token"}
        )
        assert response.status_code == 200


class TestProtectedAccess:
    async def test_requires_a_token(self, client: AsyncClient) -> None:
        response = await client.get(f"{USERS}/me")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "authentication_error"

    async def test_rejects_a_malformed_token(self, client: AsyncClient) -> None:
        response = await client.get(
            f"{USERS}/me", headers={"Authorization": "Bearer nonsense"}
        )
        assert response.status_code == 401

    async def test_rejects_a_missing_bearer_prefix(self, client: AsyncClient) -> None:
        registered = await client.post(f"{AUTH}/register", json=_registration())
        access = registered.json()["tokens"]["access_token"]
        response = await client.get(f"{USERS}/me", headers={"Authorization": access})
        assert response.status_code == 401

    async def test_returns_the_profile(
        self, client: AsyncClient, user_token: dict[str, str]
    ) -> None:
        response = await client.get(f"{USERS}/me", headers=user_token)
        assert response.status_code == 200
        assert response.json()["full_name"] == "Test User"


class TestPasswordChange:
    async def test_changes_the_password_and_revokes_sessions(
        self, client: AsyncClient
    ) -> None:
        payload = _registration()
        registered = await client.post(f"{AUTH}/register", json=payload)
        tokens = registered.json()["tokens"]
        headers = {"Authorization": f"Bearer {tokens['access_token']}"}

        response = await client.post(
            f"{AUTH}/change-password",
            headers=headers,
            json={
                "current_password": payload["password"],
                "new_password": "brandNewPassword456",
            },
        )
        assert response.status_code == 200

        # The old refresh token must no longer work.
        assert (
            await client.post(
                f"{AUTH}/refresh", json={"refresh_token": tokens["refresh_token"]}
            )
        ).status_code == 401

        # The new password must work, the old must not.
        assert (
            await client.post(
                f"{AUTH}/login",
                json={"email": payload["email"], "password": "brandNewPassword456"},
            )
        ).status_code == 200
        assert (
            await client.post(
                f"{AUTH}/login",
                json={"email": payload["email"], "password": payload["password"]},
            )
        ).status_code == 401

    async def test_rejects_a_wrong_current_password(
        self, client: AsyncClient, user_token: dict[str, str]
    ) -> None:
        response = await client.post(
            f"{AUTH}/change-password",
            headers=user_token,
            json={
                "current_password": "notTheRightOne1",
                "new_password": "brandNewPassword456",
            },
        )
        assert response.status_code == 401


class TestProfile:
    async def test_partial_update_leaves_other_fields_alone(
        self, client: AsyncClient, user_token: dict[str, str]
    ) -> None:
        before = (await client.get(f"{USERS}/me", headers=user_token)).json()

        response = await client.patch(
            f"{USERS}/me", headers=user_token, json={"home_city": "Lisbon"}
        )
        assert response.status_code == 200

        after = response.json()
        assert after["home_city"] == "Lisbon"
        assert after["full_name"] == before["full_name"]
        assert after["email"] == before["email"]

    async def test_preferences_replace(
        self, client: AsyncClient, user_token: dict[str, str]
    ) -> None:
        response = await client.put(
            f"{USERS}/me/preferences",
            headers=user_token,
            json={
                "interests": ["nature", "food"],
                "pace": "relaxed",
                "budget_level": "budget",
                "preferred_transport": "cycling",
                "day_start_time": "08:00",
                "day_end_time": "18:00",
                "max_daily_walking_km": 5.0,
            },
        )
        assert response.status_code == 200
        preferences = response.json()["preferences"]
        assert preferences["pace"] == "relaxed"
        assert set(preferences["interests"]) == {"nature", "food"}

    async def test_rejects_an_invalid_time_window(
        self, client: AsyncClient, user_token: dict[str, str]
    ) -> None:
        response = await client.put(
            f"{USERS}/me/preferences",
            headers=user_token,
            json={"day_start_time": "25:00"},
        )
        assert response.status_code == 422

    async def test_admin_listing_is_forbidden_to_ordinary_users(
        self, client: AsyncClient, user_token: dict[str, str]
    ) -> None:
        response = await client.get(USERS, headers=user_token)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "permission_denied"

    async def test_admin_can_list_users(
        self, client: AsyncClient, admin_token: dict[str, str]
    ) -> None:
        response = await client.get(USERS, headers=admin_token, params={"limit": 5})
        assert response.status_code == 200
        assert response.json()["meta"]["total"] >= 1

    async def test_deactivation_blocks_further_access(self, client: AsyncClient) -> None:
        payload = _registration()
        registered = await client.post(f"{AUTH}/register", json=payload)
        headers = {"Authorization": f"Bearer {registered.json()['tokens']['access_token']}"}

        assert (await client.delete(f"{USERS}/me", headers=headers)).status_code == 200
        assert (await client.get(f"{USERS}/me", headers=headers)).status_code == 401
        assert (
            await client.post(
                f"{AUTH}/login",
                json={"email": payload["email"], "password": payload["password"]},
            )
        ).status_code == 401
