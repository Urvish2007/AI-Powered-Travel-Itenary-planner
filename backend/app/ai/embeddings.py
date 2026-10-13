"""Embeddings and semantic retrieval.

This is the pgvector replacement. Two retrieval paths exist behind one function:

* **Atlas Vector Search** (``$vectorSearch``) when the deployment supports it -
  the production path, with the ANN index defined in ``app/db/indexes.py``.
* **In-process cosine similarity** otherwise - correct but O(n), so it is scoped
  to a city before scoring and is only appropriate at development data volumes.

Embeddings themselves come either from a provider SDK or, with no key
configured, from a deterministic hashing embedder. The hashing embedder is not
semantic in the learned sense, but it does capture lexical overlap, so retrieval
behaves sensibly in tests without a network call.
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Any

import numpy as np

from app.core.config import LLMProvider, settings
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


async def embed_text(text: str) -> list[float]:
    """Embed one string with the configured provider, or the hashing fallback."""
    if settings.llm_provider is LLMProvider.OPENAI and settings.openai_api_key:
        try:
            from openai import AsyncOpenAI

            client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
            response = await client.embeddings.create(
                model=settings.embedding_model,
                input=text,
                dimensions=settings.embedding_dimensions,
            )
            return response.data[0].embedding
        except Exception as exc:  # pragma: no cover - network
            logger.warning("embedding_provider_failed", error=str(exc))

    return hash_embed(text)


async def embed_batch(texts: list[str]) -> list[list[float]]:
    return [await embed_text(text) for text in texts]


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

    Returns raw documents with a ``search_score`` field added.
    """
    embedding = await embed_text(query)

    if settings.mongodb_vector_search_enabled:
        results = await _atlas_vector_search(embedding, city, limit, num_candidates)
        if results is not None:
            return results

    return await _cosine_fallback(embedding, city, limit)


async def _atlas_vector_search(
    embedding: list[float],
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
    filters: dict[str, Any] = {"is_active": True}
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
    embedding: list[float], city: str | None, limit: int
) -> list[dict[str, Any]]:
    """Score every candidate in-process.

    Scoped to a city wherever possible, because this loads candidate embeddings
    into memory and is linear in the number of documents scanned.
    """
    database = get_database()
    query: dict[str, Any] = {"is_active": True, "embedding": {"$ne": None}}
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
        document = dict(documents[int(position)])
        document.pop("embedding", None)
        document["search_score"] = round(float(scores[int(position)]), 6)
        results.append(document)
    return results
