"""Provider configuration and the Gemini / Groq adapters.

None of these tests touch the network or need an SDK installed. The provider
SDKs are replaced with small fakes, which is also why they run in CI where only
``requirements.txt`` is installed.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from app.ai import llm
from app.ai.llm import GeminiProvider, GroqProvider, LLMMessage, StubProvider
from app.core.config import DEFAULT_MODELS, Environment, LLMProvider, Settings, settings
from app.core.exceptions import ExternalServiceError

_KEY_VARS = ("GOOGLE_GEMINI_KEY", "GEMINI_API_KEY", "GROQ_API_KEY", "LLM_MODEL")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _KEY_VARS:
        monkeypatch.delenv(name, raising=False)
    llm.reset_llm_provider()
    yield
    llm.reset_llm_provider()


def _fresh_settings() -> Settings:
    # _env_file=None so a developer's real .env cannot leak into the assertion.
    return Settings(_env_file=None)


class TestSettingsKeys:
    def test_google_gemini_key_is_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GOOGLE_GEMINI_KEY", "g-key")
        assert _fresh_settings().google_gemini_key.get_secret_value() == "g-key"

    def test_legacy_gemini_api_key_still_accepted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "old-key")
        assert _fresh_settings().google_gemini_key.get_secret_value() == "old-key"

    def test_new_name_wins_over_legacy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GEMINI_API_KEY", "old-key")
        monkeypatch.setenv("GOOGLE_GEMINI_KEY", "new-key")
        assert _fresh_settings().google_gemini_key.get_secret_value() == "new-key"

    def test_groq_key_is_read(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "gsk_abc")
        assert _fresh_settings().groq_api_key.get_secret_value() == "gsk_abc"

    def test_keys_default_to_none(self) -> None:
        cfg = _fresh_settings()
        assert cfg.google_gemini_key is None
        assert cfg.groq_api_key is None

    def test_key_is_not_leaked_by_repr(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "gsk_super_secret")
        assert "gsk_super_secret" not in repr(_fresh_settings())


class TestModelResolution:
    @pytest.mark.parametrize("provider", [LLMProvider.GEMINI, LLMProvider.GROQ])
    def test_provider_default_is_used(
        self, monkeypatch: pytest.MonkeyPatch, provider: LLMProvider
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", provider.value)
        cfg = _fresh_settings()
        main, fast = DEFAULT_MODELS[provider]
        assert cfg.resolved_llm_model == main
        assert cfg.resolved_llm_fast_model == fast

    def test_explicit_model_overrides_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("LLM_PROVIDER", "groq")
        monkeypatch.setenv("LLM_MODEL", "some/custom-model")
        assert _fresh_settings().resolved_llm_model == "some/custom-model"

    def test_defaults_avoid_retired_models(self) -> None:
        """Guard against reintroducing IDs the providers have already retired."""
        retired = {
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "gemini-2.5-flash",
            "gemini-2.5-pro",
            "gemini-2.0-flash",
        }
        for main, fast in DEFAULT_MODELS.values():
            assert main not in retired
            assert fast not in retired


class TestProviderSelection:
    def test_gemini_without_key_falls_back_to_stub(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GEMINI)
        monkeypatch.setattr(settings, "google_gemini_key", None)
        assert isinstance(llm.get_llm_provider(), StubProvider)

    def test_groq_without_key_falls_back_to_stub(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GROQ)
        monkeypatch.setattr(settings, "groq_api_key", None)
        assert isinstance(llm.get_llm_provider(), StubProvider)

    def test_groq_is_registered(self) -> None:
        assert llm._PROVIDERS[LLMProvider.GROQ] is GroqProvider


def _fake_groq_module(captured: dict, reply: str = "hello") -> types.ModuleType:
    class _Completions:
        async def create(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=reply))],
                usage=SimpleNamespace(prompt_tokens=11, completion_tokens=7),
            )

    class AsyncGroq:
        def __init__(self, **kwargs) -> None:
            captured["client_kwargs"] = kwargs
            self.chat = SimpleNamespace(completions=_Completions())

    module = types.ModuleType("groq")
    module.AsyncGroq = AsyncGroq
    return module


class TestGroqProvider:
    async def test_complete_maps_request_and_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}
        monkeypatch.setitem(sys.modules, "groq", _fake_groq_module(captured, "hi there"))
        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GROQ)
        monkeypatch.setattr(settings, "llm_model", None)
        monkeypatch.setattr(settings, "groq_api_key", SecretStr("gsk_test"))

        provider = GroqProvider()
        result = await provider.complete(
            [LLMMessage(role="user", content="plan a trip")],
            system="be brief",
            max_tokens=256,
        )

        assert captured["client_kwargs"]["api_key"] == "gsk_test"
        assert captured["model"] == DEFAULT_MODELS[LLMProvider.GROQ][0]
        assert captured["max_completion_tokens"] == 256
        assert captured["messages"][0] == {"role": "system", "content": "be brief"}
        assert captured["messages"][1] == {"role": "user", "content": "plan a trip"}
        assert result.text == "hi there"
        assert result.provider == "groq"
        assert (result.prompt_tokens, result.completion_tokens) == (11, 7)

    async def test_complete_json_round_trip(self, monkeypatch: pytest.MonkeyPatch) -> None:
        from pydantic import BaseModel

        class Out(BaseModel):
            city: str

        captured: dict = {}
        monkeypatch.setitem(
            sys.modules, "groq", _fake_groq_module(captured, '{"city": "Lisbon"}')
        )
        monkeypatch.setattr(settings, "groq_api_key", SecretStr("gsk_test"))

        parsed, _ = await GroqProvider().complete_json(
            [LLMMessage(role="user", content="where?")], Out
        )
        assert parsed.city == "Lisbon"

    def test_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(sys.modules, "groq", _fake_groq_module({}))
        monkeypatch.setattr(settings, "groq_api_key", None)
        with pytest.raises(ExternalServiceError, match="GROQ_API_KEY"):
            GroqProvider()


class TestGeminiProvider:
    async def test_complete_uses_new_key_and_default_model(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        captured: dict = {}

        class _Models:
            async def generate_content(self, **kwargs):
                captured.update(kwargs)
                return SimpleNamespace(
                    text="bonjour",
                    usage_metadata=SimpleNamespace(
                        prompt_token_count=5, candidates_token_count=3
                    ),
                )

        class Client:
            def __init__(self, **kwargs) -> None:
                captured["client_kwargs"] = kwargs
                self.aio = SimpleNamespace(models=_Models())

        genai = types.ModuleType("google.genai")
        genai.Client = Client
        google = types.ModuleType("google")
        google.genai = genai
        monkeypatch.setitem(sys.modules, "google", google)
        monkeypatch.setitem(sys.modules, "google.genai", genai)

        monkeypatch.setattr(settings, "llm_provider", LLMProvider.GEMINI)
        monkeypatch.setattr(settings, "llm_model", None)
        monkeypatch.setattr(settings, "google_gemini_key", SecretStr("g-test"))

        result = await GeminiProvider().complete(
            [LLMMessage(role="user", content="hi")], system="sys"
        )

        assert captured["client_kwargs"]["api_key"] == "g-test"
        assert captured["model"] == DEFAULT_MODELS[LLMProvider.GEMINI][0]
        assert result.text == "bonjour"
        assert result.provider == "gemini"
        assert (result.prompt_tokens, result.completion_tokens) == (5, 3)

    def test_missing_key_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        genai = types.ModuleType("google.genai")
        genai.Client = lambda **_: None
        google = types.ModuleType("google")
        google.genai = genai
        monkeypatch.setitem(sys.modules, "google", google)
        monkeypatch.setitem(sys.modules, "google.genai", genai)
        monkeypatch.setattr(settings, "google_gemini_key", None)
        with pytest.raises(ExternalServiceError, match="GOOGLE_GEMINI_KEY"):
            GeminiProvider()


class TestRateLimitExemptions:
    def test_health_paths_use_the_real_prefix(self) -> None:
        from app.middleware.rate_limit import EXEMPT_PATHS

        prefix = settings.api_v1_prefix
        assert f"{prefix}/health" in EXEMPT_PATHS
        assert f"{prefix}/health/ready" in EXEMPT_PATHS

    async def test_probes_are_never_throttled_but_other_routes_are(self) -> None:
        from app.middleware.rate_limit import RateLimitMiddleware

        async def ok(_request):
            return PlainTextResponse("ok")

        prefix = settings.api_v1_prefix
        app = Starlette(
            routes=[
                Route(f"{prefix}/health", ok),
                Route(f"{prefix}/health/ready", ok),
                Route(f"{prefix}/trips", ok),
            ]
        )
        app.add_middleware(RateLimitMiddleware, limit=2, window=60)

        async with AsyncClient(
            transport=ASGITransport(app=app), base_url="http://test"
        ) as client:
            for _ in range(10):
                assert (await client.get(f"{prefix}/health")).status_code == 200
                assert (await client.get(f"{prefix}/health/ready")).status_code == 200

            assert (await client.get(f"{prefix}/trips")).status_code == 200
            assert (await client.get(f"{prefix}/trips")).status_code == 200
            assert (await client.get(f"{prefix}/trips")).status_code == 429


class TestProductionDocs:
    async def _status(self, path: str) -> int:
        from app.main import create_app

        async with AsyncClient(
            transport=ASGITransport(app=create_app()), base_url="http://test"
        ) as client:
            return (await client.get(path, follow_redirects=False)).status_code

    async def test_docs_are_available_locally(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "environment", Environment.LOCAL)
        assert await self._status("/docs") == 200
        assert await self._status("/redoc") == 200

    async def test_docs_are_hidden_in_production(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "environment", Environment.PRODUCTION)
        assert await self._status("/docs") == 404
        assert await self._status("/redoc") == 404
        assert await self._status("/") == 404
