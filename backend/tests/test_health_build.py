"""/health reports the commit it runs, which is what the deploy gate waits for."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings, settings


@pytest.mark.parametrize("variable", ["BUILD_SHA", "RENDER_GIT_COMMIT"])
def test_build_comes_from_the_docker_arg_or_render(
    monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    for name in ("BUILD_SHA", "RENDER_GIT_COMMIT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, "abc1234def")
    assert Settings(_env_file=None).build_sha == "abc1234def"


async def test_liveness_reports_the_build(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.main import create_app

    monkeypatch.setattr(settings, "build_sha", "abc1234def")
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        body = (await client.get(f"{settings.api_v1_prefix}/health")).json()
    assert body["build"] == "abc1234def"


async def test_an_unrecorded_build_is_null_not_a_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.main import create_app

    monkeypatch.setattr(settings, "build_sha", None)
    async with AsyncClient(
        transport=ASGITransport(app=create_app()), base_url="http://test"
    ) as client:
        body = (await client.get(f"{settings.api_v1_prefix}/health")).json()
    assert body["build"] is None
