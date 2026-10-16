"""Embeddings and semantic retrieval.

This is the pgvector replacement. Two retrieval paths exist behind one function:

* **Atlas Vector Search** (``$vectorSearch``) when the deployment supports it -
  the production path, with the ANN index defined in ``app/db/indexes.py``.
* **In-process cosine similarity** otherwise - correct but O(n), so it is scoped
  to a city before scoring and is only appropriate at development data volumes.

Embeddings come from the provider named by ``EMBEDDING_PROVIDER``, which is
independent of the LLM: ``hash`` (the default - deterministic, lexical, no key
or network, so it works with nothing but a Groq key), ``openai``, ``gemini`` or
``local`` (sentence-transformers, if installed).

Vectors from different embedders are not comparable, so every stored vector
records its **space** - provider, model and dimension - and search only ever
compares vectors from the configured space. There is no silent fallback to
another embedder: a provider failure is an error, not a quietly mixed index.
Switching provider therefore needs a re-embed (``python -m scripts.reembed``),
which is idempotent and resumable.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Literal, Protocol

import numpy as np

from app.core.config import EmbeddingProviderName, settings
from app.core.exceptions import ExternalServiceError, RateLimitError
from app.db.mongodb import get_database
from app.observability.logging import get_logger

logger = get_logger(__name__)

_TOKEN = re.compile(r"[a-z0-9]+")

# Words that appear in nearly every place description and so carry no signal.
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "to",
        "was",
        "were",
        "will",
        "with",
        "you",
        "your",
        "this",
        "these",
        "those",
        "there",
        "here",
        "best",
        "top",
        "most",
        "very",
        "one",
        "two",
        "more",
        "also",
        "can",
        "just",
        "about",
        "into",
        "over",
        "under",
        "between",
        "during",
    }
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS and len(t) > 2]


def hash_embed(text: str, dimensions: int | None = None) -> list[float]:
    """Deterministic hashing-trick embedding, L2-normalised.

    Each token is hashed to a dimension and a sign, then accumulated with a
    sublinear term-frequency weight. Two texts sharing vocabulary land close
    together, which is enough for the retrieval layer to be exercised honestly
    without an embedding API.
    """
    dimensions = dimensions or settings.embedding_dimensions
    vector = np.zeros(dimensions, dtype=np.float32)

    counts: dict[str, int] = {}
    for token in tokenize(text):
        counts[token] = counts.get(token, 0) + 1

    for token, count in counts.items():
        digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
        position = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[position] += sign * (1.0 + math.log(count))

    norm = float(np.linalg.norm(vector))
    if norm > 0:
        vector /= norm
    return vector.tolist()


# The hash embedder's algorithm version. Bump it if ``hash_embed`` changes, so
# vectors made by the old recipe are re-embedded rather than compared.
HASH_MODEL = "hash-v1"

Kind = Literal["document", "query"]


class EmbeddingUnavailableError(ExternalServiceError):
    """The embedding provider could not produce usable vectors."""

    message = "Semantic search is unavailable right now."


@dataclass(frozen=True, slots=True)
class EmbeddingSpace:
    """Which embedder made a vector. Vectors are comparable only within one."""

    provider: str
    model: str
    dimensions: int

    @property
    def key(self) -> str:
        return f"{self.provider}:{self.model}:{self.dimensions}"


class Embedder(Protocol):
    space: EmbeddingSpace
    # The most texts one provider call accepts.
    max_batch: int

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]: ...


class HashEmbedder:
    max_batch = 10_000

    def __init__(self, dimensions: int) -> None:
        self.space = EmbeddingSpace("hash", HASH_MODEL, dimensions)

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        return [hash_embed(text, self.space.dimensions) for text in texts]


class OpenAIEmbedder:
    max_batch = 512

    def __init__(self, model: str, dimensions: int) -> None:
        from openai import AsyncOpenAI

        if settings.openai_api_key is None:  # pragma: no cover - settings validate this
            raise EmbeddingUnavailableError("OPENAI_API_KEY is not configured.")
        self.space = EmbeddingSpace("openai", model, dimensions)
        # The SDK retries timeouts, 429 and 5xx with backoff.
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.embedding_timeout_seconds,
            max_retries=settings.embedding_max_retries,
        )

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        response = await self._client.embeddings.create(
            model=self.space.model, input=texts, dimensions=self.space.dimensions
        )
        ordered = sorted(response.data, key=lambda item: item.index)
        return [list(item.embedding) for item in ordered]


class GeminiEmbedder:
    # The Gemini API accepts up to 100 texts per embedding request.
    max_batch = 100

    def __init__(self, model: str, dimensions: int) -> None:
        from google import genai

        if settings.google_gemini_key is None:  # pragma: no cover - settings validate
            raise EmbeddingUnavailableError("GOOGLE_GEMINI_KEY is not configured.")
        self.space = EmbeddingSpace("gemini", model, dimensions)
        self._client = genai.Client(api_key=settings.google_gemini_key.get_secret_value())

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        async def call() -> Any:
            return await asyncio.wait_for(
                self._client.aio.models.embed_content(
                    model=self.space.model,
                    contents=texts,
                    config={
                        "output_dimensionality": self.space.dimensions,
                        # Asymmetric retrieval: queries and documents are
                        # embedded for their role, which improves recall.
                        "task_type": (
                            "RETRIEVAL_QUERY" if kind == "query" else "RETRIEVAL_DOCUMENT"
                        ),
                    },
                ),
                timeout=settings.embedding_timeout_seconds,
            )

        response = await with_backoff(call, provider="gemini")
        return [list(item.values or []) for item in response.embeddings or []]


class LocalEmbedder:
    """A sentence-transformers model on this machine. Not installed by default."""

    max_batch = 256

    def __init__(self, model: str, dimensions: int) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise EmbeddingUnavailableError(
                "EMBEDDING_PROVIDER=local needs `pip install sentence-transformers`."
            ) from exc
        self.space = EmbeddingSpace("local", model, dimensions)
        self._model = SentenceTransformer(model)

    async def embed(self, texts: list[str], kind: Kind) -> list[list[float]]:
        vectors = await asyncio.to_thread(
            self._model.encode, texts, normalize_embeddings=True
        )
        return [list(map(float, vector)) for vector in vectors]


_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})


def _status_of(exc: BaseException) -> int | None:
    for attribute in ("status_code", "code", "status"):
        value = getattr(exc, attribute, None)
        if isinstance(value, int):
            return value
    return None


async def with_backoff[T](call: Callable[[], Awaitable[T]], *, provider: str) -> T:
    """Run ``call``, retrying timeouts, 429 and 5xx with exponential backoff.

    For providers whose SDK does not retry by itself. A rate limit that
    outlasts the retries becomes a 429 the caller can report honestly.
    """
    attempts = 1 + max(0, settings.embedding_max_retries)
    for attempt in range(attempts):
        try:
            return await call()
        except (TimeoutError, ConnectionError) as exc:
            status: int | None = None
            error: BaseException = exc
        except Exception as exc:
            status = _status_of(exc)
            if status not in _RETRYABLE_STATUS:
                raise
            error = exc
        if attempt == attempts - 1:
            if status == 429:
                raise RateLimitError(
                    f"The embedding provider ({provider}) is rate-limiting requests. "
                    "Try again in a minute.",
                    details={"provider": provider},
                ) from error
            raise EmbeddingUnavailableError(
                f"The embedding provider ({provider}) did not respond.",
                details={"provider": provider, "status": status},
            ) from error
        delay = 2**attempt
        logger.warning(
            "embedding_retry",
            provider=provider,
            status=status,
            attempt=attempt + 1,
            delay_seconds=delay,
        )
        await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # pragma: no cover


_embedder: Embedder | None = None
_embedder_config: tuple[Any, ...] | None = None


def get_embedder() -> Embedder:
    """The configured embedder, built once per configuration."""
    global _embedder, _embedder_config
    config = (
        settings.embedding_provider,
        settings.embedding_model,
        settings.embedding_dimensions,
    )
    if _embedder is not None and _embedder_config == config:
        return _embedder

    provider = settings.embedding_provider
    model = settings.embedding_model or ""
    dimensions = settings.embedding_dimensions
    try:
        if provider is EmbeddingProviderName.OPENAI:
            embedder: Embedder = OpenAIEmbedder(model, dimensions)
        elif provider is EmbeddingProviderName.GEMINI:
            embedder = GeminiEmbedder(model, dimensions)
        elif provider is EmbeddingProviderName.LOCAL:
            embedder = LocalEmbedder(model, dimensions)
        else:
            embedder = HashEmbedder(dimensions)
    except ImportError as exc:
        raise EmbeddingUnavailableError(
            f"EMBEDDING_PROVIDER={provider} needs its SDK; run "
            "`pip install -r requirements-ai.txt`."
        ) from exc
    _embedder, _embedder_config = embedder, config
    return embedder


def reset_embedder() -> None:
    """Forget the cached embedder. For tests that change the configuration."""
    global _embedder, _embedder_config
    _embedder, _embedder_config = None, None


def current_space() -> EmbeddingSpace:
    """The space new vectors are written in and searches are run against."""
    return get_embedder().space


async def embed_documents(texts: list[str]) -> list[list[float]]:
    """Embed many texts, batched to what the provider accepts.

    Raises:
        EmbeddingUnavailableError: when the provider fails or returns vectors
            of the wrong shape.
        RateLimitError: when the provider is still rate-limiting after retries.
    """
    return await _embed(texts, "document")


async def embed_query(text: str) -> list[float]:
    """Embed a search query, in the same space as the stored documents."""
    return (await _embed([text], "query"))[0]


async def embed_text(text: str) -> list[float]:
    """Embed one document. Kept for callers that embed a single place."""
    return (await embed_documents([text]))[0]


async def _embed(texts: list[str], kind: Kind) -> list[list[float]]:
    if not texts:
        return []
    embedder = get_embedder()
    size = max(1, min(settings.embedding_batch_size, embedder.max_batch))
    vectors: list[list[float]] = []
    for start in range(0, len(texts), size):
        batch = texts[start : start + size]
        started = time.perf_counter()
        try:
            produced = await embedder.embed(batch, kind)
        except (EmbeddingUnavailableError, RateLimitError):
            raise
        except Exception as exc:
            logger.warning(
                "embedding_failed",
                provider=embedder.space.provider,
                model=embedder.space.model,
                batch=len(batch),
                error_type=type(exc).__name__,
            )
            raise EmbeddingUnavailableError(
                f"The embedding provider ({embedder.space.provider}) failed.",
                details={"provider": embedder.space.provider},
            ) from exc
        vectors.extend(_validated(produced, len(batch), embedder.space))
        logger.debug(
            "embedding_batch",
            provider=embedder.space.provider,
            batch=len(batch),
            elapsed_ms=round((time.perf_counter() - started) * 1000),
        )
    return vectors


def _validated(
    vectors: list[list[float]], expected: int, space: EmbeddingSpace
) -> list[list[float]]:
    """Reject a reply that would corrupt the index rather than store it."""
    if len(vectors) != expected:
        raise EmbeddingUnavailableError(
            f"The embedding provider returned {len(vectors)} vectors for {expected} texts."
        )
    for vector in vectors:
        if len(vector) != space.dimensions or not all(map(math.isfinite, vector)):
            raise EmbeddingUnavailableError(
                "The embedding provider returned vectors of the wrong size; check "
                "that EMBEDDING_DIMENSIONS is supported by EMBEDDING_MODEL.",
                details={"expected_dimensions": space.dimensions},
            )
    return [[float(value) for value in vector] for vector in vectors]


def cosine_similarity(a: list[float] | np.ndarray, b: list[float] | np.ndarray) -> float:
    """Cosine similarity, safe on zero vectors."""
    va, vb = np.asarray(a, dtype=np.float32), np.asarray(b, dtype=np.float32)
    denominator = float(np.linalg.norm(va) * np.linalg.norm(vb))
    if denominator == 0.0:
        return 0.0
    return float(np.dot(va, vb) / denominator)


def place_embedding_text(
    name: str, description: str, category: str, tags: list[str], city: str
) -> str:
    """The canonical string embedded for a place.

    Kept in one place so that re-embedding an existing corpus is guaranteed to
    use the same recipe as the original ingest.
    """
    return f"{name}. {city}. {category}. {' '.join(tags)}. {description}"


async def vector_search(
    query: str,
    *,
    city: str | None = None,
    limit: int = 20,
    num_candidates: int | None = None,
) -> list[dict[str, Any]]:
    """Semantic search over places, with the best available backend.

    Returns raw documents with a ``search_score`` field added. Only places
    embedded in the configured space are considered; places not yet
    re-embedded are invisible here rather than compared across spaces.

    Raises:
        EmbeddingUnavailableError, RateLimitError: when the query cannot be
            embedded. Callers decide whether search is essential.
    """
    embedding = await embed_query(query)
    space = current_space().key

    if settings.mongodb_vector_search_enabled:
        results = await _atlas_vector_search(embedding, space, city, limit, num_candidates)
        if results is not None:
            return results

    return await _cosine_fallback(embedding, space, city, limit)


async def _atlas_vector_search(
    embedding: list[float],
    space: str,
    city: str | None,
    limit: int,
    num_candidates: int | None,
) -> list[dict[str, Any]] | None:
    """Run ``$vectorSearch``; return None when the deployment rejects it."""
    database = get_database()
    stage: dict[str, Any] = {
        "index": settings.mongodb_vector_index_name,
        "path": "embedding",
        "queryVector": embedding,
        # Atlas needs a wider candidate pool than the requested limit to keep
        # approximate recall high.
        "numCandidates": num_candidates or max(100, limit * 10),
        "limit": limit,
    }
    filters: dict[str, Any] = {"is_active": True, "embedding_space": space}
    if city:
        filters["city"] = city
    stage["filter"] = filters

    pipeline = [
        {"$vectorSearch": stage},
        {"$addFields": {"search_score": {"$meta": "vectorSearchScore"}}},
        {"$project": {"embedding": 0}},
    ]
    try:
        return await database["places"].aggregate(pipeline).to_list(length=limit)
    except Exception as exc:
        logger.warning(
            "atlas_vector_search_failed", error=str(exc), falling_back_to="cosine"
        )
        return None


async def _cosine_fallback(
    embedding: list[float], space: str, city: str | None, limit: int
) -> list[dict[str, Any]]:
    """Score every candidate in-process.

    Scoped to a city wherever possible, because this loads candidate embeddings
    into memory and is linear in the number of documents scanned.
    """
    database = get_database()
    query: dict[str, Any] = {
        "is_active": True,
        "embedding": {"$ne": None},
        "embedding_space": space,
    }
    if city:
        query["city"] = city

    documents = await database["places"].find(query).to_list(length=2000)
    if not documents:
        return []

    query_vector = np.asarray(embedding, dtype=np.float32)
    query_norm = float(np.linalg.norm(query_vector))
    if query_norm == 0.0:
        return []

    matrix = np.asarray([doc["embedding"] for doc in documents], dtype=np.float32)
    norms = np.linalg.norm(matrix, axis=1)
    norms[norms == 0.0] = 1.0
    scores = (matrix @ query_vector) / (norms * query_norm)

    ranked = np.argsort(-scores)[:limit]
    results: list[dict[str, Any]] = []
    for position in ranked:
        score = float(scores[int(position)])
        # A zero score means the query shares no vocabulary at all with this
        # place, so its position in the ranking is arbitrary. Returning those
        # would dress up "no matches" as three confident suggestions, which is
        # worse than an empty result the caller can report honestly.
        if score <= 0.0:
            break
        document = dict(documents[int(position)])
        document.pop("embedding", None)
        document["search_score"] = round(score, 6)
        results.append(document)
    return results
