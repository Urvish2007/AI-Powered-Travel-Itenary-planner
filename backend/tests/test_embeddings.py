"""Embedding providers, vector-space bookkeeping and the re-embed.

The provider SDKs are replaced with small fakes, so nothing touches the network.
The database tests need MongoDB and skip without it (see ``conftest.py``).
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import SecretStr

from app.ai import embeddings
from app.ai.embeddings import (
    EmbeddingSpace,
    EmbeddingUnavailableError,
    GeminiEmbedder,
    OpenAIEmbedder,
    embed_documents,
    with_backoff,
)
from app.core.config import EmbeddingProviderName, Settings, settings
from app.core.exceptions import RateLimitError

DIMS = settings.embedding_dimensions


class FakeEmbedder:
    """Records each call; vectors depend only on the text, like a real one."""

    def __init__(self, dims: int = DIMS, name: str = "fake", max_batch: int = 1000) -> None:
        self.space = EmbeddingSpace(name, "fake-model", dims)
        self.max_batch = max_batch
        self.calls: list[tuple[int, str]] = []
        self.fail_after: int | None = None

    async def embed(self, texts: list[str], kind: str) -> list[list[float]]:
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RateLimitError("The embedding provider (fake) is rate-limiting requests.")
        self.calls.append((len(texts), kind))
        return [embeddings.hash_embed(text, self.space.dimensions) for text in texts]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> FakeEmbedder:
    embedder = FakeEmbedder()
    monkeypatch.setattr(embeddings, "get_embedder", lambda: embedder)
    return embedder


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch: pytest.MonkeyPatch) -> None:
    async def instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(embeddings.asyncio, "sleep", instant)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class TestConfiguration:
    def test_the_default_needs_no_key(self) -> None:
        cfg = Settings(_env_file=None)
        assert cfg.embedding_provider is EmbeddingProviderName.HASH

    def test_default_space_is_the_hash_embedder(self) -> None:
        embeddings.reset_embedder()
        assert embeddings.current_space().key == f"hash:hash-v1:{DIMS}"

    @pytest.mark.parametrize(
        ("env", "message"),
        [
            ({"EMBEDDING_PROVIDER": "gemini", "GOOGLE_GEMINI_KEY": "k"}, "EMBEDDING_MODEL"),
            ({"EMBEDDING_PROVIDER": "gemini", "EMBEDDING_MODEL": "m"}, "GOOGLE_GEMINI_KEY"),
            ({"EMBEDDING_PROVIDER": "openai", "EMBEDDING_MODEL": "m"}, "OPENAI_API_KEY"),
        ],
    )
    def test_learned_providers_must_be_fully_configured(
        self, monkeypatch: pytest.MonkeyPatch, env: dict[str, str], message: str
    ) -> None:
        for name in ("GOOGLE_GEMINI_KEY", "GEMINI_API_KEY", "OPENAI_API_KEY"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        with pytest.raises(ValueError, match=message):
            Settings(_env_file=None)


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


class TestEmbedding:
    async def test_batches_to_the_provider_limit(
        self, fake: FakeEmbedder, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake.max_batch = 3
        vectors = await embed_documents([f"text {i}" for i in range(7)])
        assert len(vectors) == 7
        assert [size for size, _ in fake.calls] == [3, 3, 1]
        assert {kind for _, kind in fake.calls} == {"document"}

    async def test_queries_are_embedded_as_queries(self, fake: FakeEmbedder) -> None:
        await embeddings.embed_query("museums")
        assert fake.calls == [(1, "query")]

    async def test_vectors_of_the_wrong_size_are_rejected(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        wrong = FakeEmbedder()
        wrong.space = EmbeddingSpace("fake", "fake-model", DIMS + 1)  # claims more
        wrong.embed = lambda texts, kind: _resolved([[0.1] * DIMS for _ in texts])  # type: ignore[method-assign]
        monkeypatch.setattr(embeddings, "get_embedder", lambda: wrong)
        with pytest.raises(EmbeddingUnavailableError, match="wrong size"):
            await embed_documents(["a"])

    async def test_a_short_reply_is_rejected(self, monkeypatch: pytest.MonkeyPatch) -> None:
        short = FakeEmbedder()
        short.embed = lambda texts, kind: _resolved([])  # type: ignore[method-assign]
        monkeypatch.setattr(embeddings, "get_embedder", lambda: short)
        with pytest.raises(EmbeddingUnavailableError, match="0 vectors for 2 texts"):
            await embed_documents(["a", "b"])

    async def test_provider_failure_is_an_error_not_a_silent_fallback(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        broken = FakeEmbedder(name="openai")

        async def boom(texts: list[str], kind: str) -> list[list[float]]:
            raise RuntimeError("socket closed")

        broken.embed = boom  # type: ignore[method-assign]
        monkeypatch.setattr(embeddings, "get_embedder", lambda: broken)
        with pytest.raises(EmbeddingUnavailableError, match="openai"):
            await embed_documents(["a"])


async def _resolved(value: Any) -> Any:
    return value


class _StatusError(Exception):
    def __init__(self, code: int) -> None:
        super().__init__(f"HTTP {code}")
        self.code = code


class TestBackoff:
    async def test_transient_errors_are_retried(self) -> None:
        attempts: list[int] = []

        async def flaky() -> str:
            attempts.append(1)
            if len(attempts) < 3:
                raise _StatusError(503)
            return "ok"

        assert await with_backoff(flaky, provider="gemini") == "ok"
        assert len(attempts) == 3

    async def test_a_lasting_rate_limit_is_reported_as_one(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(settings, "embedding_max_retries", 2)
        attempts: list[int] = []

        async def limited() -> None:
            attempts.append(1)
            raise _StatusError(429)

        with pytest.raises(RateLimitError, match="rate-limiting"):
            await with_backoff(limited, provider="gemini")
        assert len(attempts) == 3

    async def test_a_client_error_is_not_retried(self) -> None:
        attempts: list[int] = []

        async def bad_request() -> None:
            attempts.append(1)
            raise _StatusError(400)

        with pytest.raises(_StatusError):
            await with_backoff(bad_request, provider="gemini")
        assert len(attempts) == 1


# ---------------------------------------------------------------------------
# Provider adapters (fake SDKs)
# ---------------------------------------------------------------------------


class TestGeminiEmbedder:
    async def test_request_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: list[dict[str, Any]] = []

        class _Models:
            async def embed_content(self, **kwargs: Any) -> Any:
                captured.append(kwargs)
                return SimpleNamespace(
                    embeddings=[
                        SimpleNamespace(values=[0.5] * 8) for _ in kwargs["contents"]
                    ]
                )

        class Client:
            def __init__(self, **kwargs: Any) -> None:
                self.aio = SimpleNamespace(models=_Models())

        genai = types.ModuleType("google.genai")
        genai.Client = Client  # type: ignore[attr-defined]
        google = types.ModuleType("google")
        google.genai = genai  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "google", google)
        monkeypatch.setitem(sys.modules, "google.genai", genai)
        monkeypatch.setattr(settings, "google_gemini_key", SecretStr("g-key"))

        embedder = GeminiEmbedder("gemini-embedding-test", 8)
        await embedder.embed(["a", "b"], "document")
        await embedder.embed(["q"], "query")

        assert captured[0]["model"] == "gemini-embedding-test"
        assert captured[0]["contents"] == ["a", "b"]
        assert captured[0]["config"]["output_dimensionality"] == 8
        assert captured[0]["config"]["task_type"] == "RETRIEVAL_DOCUMENT"
        assert captured[1]["config"]["task_type"] == "RETRIEVAL_QUERY"
        assert embedder.space.key == "gemini:gemini-embedding-test:8"


class TestOpenAIEmbedder:
    async def test_request_shape_and_order(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        class _Embeddings:
            async def create(self, **kwargs: Any) -> Any:
                captured.update(kwargs)
                # Out of order on purpose: results are matched by index.
                return SimpleNamespace(
                    data=[
                        SimpleNamespace(index=1, embedding=[2.0] * 4),
                        SimpleNamespace(index=0, embedding=[1.0] * 4),
                    ]
                )

        class AsyncOpenAI:
            def __init__(self, **kwargs: Any) -> None:
                captured["client_kwargs"] = kwargs
                self.embeddings = _Embeddings()

        openai = types.ModuleType("openai")
        openai.AsyncOpenAI = AsyncOpenAI  # type: ignore[attr-defined]
        monkeypatch.setitem(sys.modules, "openai", openai)
        monkeypatch.setattr(settings, "openai_api_key", SecretStr("sk-test"))

        vectors = await OpenAIEmbedder("text-embedding-test", 4).embed(
            ["a", "b"], "document"
        )

        assert vectors == [[1.0] * 4, [2.0] * 4]
        assert captured["dimensions"] == 4
        assert captured["client_kwargs"]["max_retries"] == settings.embedding_max_retries


# ---------------------------------------------------------------------------
# Re-embed and search, on a real database
# ---------------------------------------------------------------------------


async def _place(name: str, **fields: Any) -> Any:
    from app.models.base import GeoPoint
    from app.models.enums import PlaceCategory
    from app.models.place import Place

    place = Place(
        name=name,
        slug=name.lower().replace(" ", "-"),
        description=f"{name} is a quiet museum of old maps and globes.",
        category=PlaceCategory.MUSEUM,
        tags=[],
        location=GeoPoint.from_lat_lng(10.0, 10.0),
        city="Testville",
        country="Nowhere",
        timezone="UTC",
        **fields,
    )
    await place.insert()
    return place


@pytest.mark.db
class TestReembed:
    async def test_is_idempotent_and_upgrades_legacy_vectors(
        self, database: Any, fake: FakeEmbedder
    ) -> None:
        from app.models.place import Place
        from app.services.place_service import reembed_places

        await _place("Map Museum")
        await _place("Globe Hall")
        # Written before spaces existed: a vector, but no record of its maker.
        await _place("Atlas House", embedding=[0.1] * DIMS, embedding_model="old")

        first = await reembed_places()
        assert (first.written, first.remaining, first.stopped) == (3, 0, None)
        assert first.space == fake.space.key
        for place in await Place.find_all().to_list():
            assert place.embedding_space == fake.space.key
            assert place.embedding_model == "fake-model"

        second = await reembed_places()
        assert (second.written, second.remaining) == (0, 0)

    async def test_dry_run_only_counts(self, database: Any, fake: FakeEmbedder) -> None:
        from app.services.place_service import reembed_places

        await _place("Map Museum")
        report = await reembed_places(dry_run=True)
        assert (report.written, report.remaining) == (0, 1)
        assert fake.calls == []

    async def test_a_rate_limit_stops_the_run_and_a_rerun_resumes(
        self, database: Any, fake: FakeEmbedder
    ) -> None:
        from app.services.place_service import reembed_places

        for i in range(5):
            await _place(f"Museum {i}")
        fake.fail_after = 1  # the second batch is refused

        stopped = await reembed_places(batch_size=2)
        assert stopped.written == 2
        assert stopped.remaining == 3
        assert stopped.stopped and "rate-limiting" in stopped.stopped

        fake.fail_after = None
        resumed = await reembed_places(batch_size=2)
        assert (resumed.written, resumed.remaining, resumed.stopped) == (3, 0, None)

    async def test_search_never_compares_across_spaces(
        self, database: Any, fake: FakeEmbedder
    ) -> None:
        from app.services.place_service import reembed_places

        await _place("Map Museum")
        await reembed_places()
        # Same text, so the same vector - but recorded as another embedder's.
        await _place(
            "Map Museum Annex",
            embedding=embeddings.hash_embed("Map Museum Annex museum maps", DIMS),
            embedding_space="other:model:384",
        )

        results = await embeddings.vector_search("museum of old maps", city="Testville")
        assert [r["name"] for r in results] == ["Map Museum"]

    async def test_a_failed_embedding_still_saves_the_place(
        self, database: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.models.place import Place
        from app.services.place_service import attach_embedding

        failing = FakeEmbedder()
        failing.fail_after = 0
        monkeypatch.setattr(embeddings, "get_embedder", lambda: failing)

        place = await _place("Map Museum", embedding=[0.2] * DIMS, embedding_space="x")
        assert await attach_embedding(place) is False
        await place.save()

        stored = await Place.get(place.id)
        # Not left holding a vector for text that may have changed.
        assert stored.embedding is None and stored.embedding_space is None
