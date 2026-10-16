"""LLM provider abstraction.

One interface, five implementations. ``StubProvider`` is deterministic and needs
no network or API key, which is what lets the full planning pipeline - and its
tests - run in CI and on a laptop with nothing configured. The real providers are
imported lazily so their SDKs stay optional dependencies.
"""

from __future__ import annotations

import abc
import hashlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import LLMProvider, settings
from app.core.exceptions import ExternalServiceError, RateLimitError
from app.observability.logging import get_logger
from app.observability.metrics import LLM_CALLS, LLM_TOKENS

logger = get_logger(__name__)

TModel = TypeVar("TModel", bound=BaseModel)


@dataclass(slots=True)
class LLMMessage:
    role: str  # "user" | "assistant"
    content: str


@dataclass(slots=True)
class LLMResponse:
    text: str
    provider: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    latency_ms: int = 0
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


class BaseLLMProvider(abc.ABC):
    """Contract every provider implements."""

    name: str = "base"

    @abc.abstractmethod
    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse: ...

    async def complete_json(
        self,
        messages: list[LLMMessage],
        schema: type[TModel],
        *,
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> tuple[TModel, LLMResponse]:
        """Complete and parse the reply into ``schema``.

        Raises:
            ExternalServiceError: when the model's reply is not valid JSON for
                the requested schema.
        """
        instruction = (f"{system}\n\n" if system else "") + _json_instruction(schema)
        response = await self._complete_for_json(
            messages, system=instruction, max_tokens=max_tokens
        )
        payload = extract_json(response.text)
        if payload is None:
            raise ExternalServiceError(
                f"{self.name} returned no parsable JSON object.",
                details={"preview": response.text[:400]},
            )
        try:
            return schema.model_validate(payload), response
        except ValidationError as exc:
            raise ExternalServiceError(
                f"{self.name} returned JSON that does not match {schema.__name__}.",
                details={"errors": exc.errors()[:5]},
            ) from exc

    async def _complete_for_json(
        self,
        messages: list[LLMMessage],
        *,
        system: str,
        max_tokens: int | None,
    ) -> LLMResponse:
        """The completion behind :meth:`complete_json`.

        A provider with a native JSON mode overrides this to switch it on. The
        reply is validated against the schema either way: JSON mode makes a
        well-formed object likely, never a correct one.
        """
        return await self.complete(messages, system=system, max_tokens=max_tokens)


def _json_instruction(schema: type[BaseModel]) -> str:
    return (
        "Reply with a single JSON object and nothing else - no prose, no code "
        "fences. It must validate against this JSON Schema:\n"
        f"{json.dumps(schema.model_json_schema(), indent=2)}"
    )


_JSON_BLOCK = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first JSON object out of a model reply.

    Handles the three shapes models actually emit: bare JSON, JSON inside a
    fenced code block, and JSON preceded by a sentence of commentary.
    """
    text = text.strip()
    if not text:
        return None

    candidates: list[str] = []
    fenced = _JSON_BLOCK.search(text)
    if fenced:
        candidates.append(fenced.group(1).strip())
    candidates.append(text)

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return None


# ---------------------------------------------------------------------------
# Stub
# ---------------------------------------------------------------------------


class StubProvider(BaseLLMProvider):
    """Deterministic provider used in tests and key-less local development.

    It never calls the network. Replies are derived from a hash of the prompt so
    that the same input always produces the same output - which is exactly what
    you want when asserting on pipeline behaviour rather than on model prose.
    """

    name = "stub"

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        started = time.perf_counter()
        prompt = "\n".join(m.content for m in messages)
        digest = hashlib.sha256((system or "").encode() + prompt.encode()).hexdigest()

        text = _stub_reply(system or "", prompt, digest)
        latency = int((time.perf_counter() - started) * 1000)

        LLM_CALLS.labels(self.name, "success").inc()
        return LLMResponse(
            text=text,
            provider=self.name,
            model="stub",
            prompt_tokens=len(prompt) // 4,
            completion_tokens=len(text) // 4,
            latency_ms=latency,
        )


def _stub_reply(system: str, prompt: str, digest: str) -> str:
    """Produce a schema-shaped reply for whichever task was requested.

    The task is inferred from markers the prompt builders place in the system
    prompt, so the stub stays in step with the real prompts.
    """
    if "TASK:PARSE_INTENT" in system:
        return json.dumps(_stub_intent(prompt))
    if "TASK:SELECT_PLACES" in system:
        return json.dumps(_stub_selection(prompt, digest))
    if "TASK:NARRATE" in system:
        return json.dumps(_stub_narration(prompt, digest))
    return json.dumps({"response": "stub", "digest": digest[:12]})


def _stub_intent(prompt: str) -> dict[str, Any]:
    """Keyword-match the free-text brief into the structured intent schema."""
    lowered = prompt.lower()
    interest_keywords = {
        "history": ("history", "historic", "heritage", "ancient", "castle"),
        "art": ("art", "gallery", "painting", "sculpture"),
        "food": ("food", "eat", "cuisine", "restaurant", "culinary", "foodie"),
        "nature": ("nature", "park", "hike", "outdoor", "garden", "mountain"),
        "adventure": ("adventure", "thrill", "climb", "dive", "surf"),
        "nightlife": ("nightlife", "bar", "club", "party"),
        "shopping": ("shopping", "market", "boutique", "shop"),
        "relaxation": ("relax", "spa", "slow", "chill", "unwind"),
        "family": ("family", "kids", "children", "child-friendly"),
        "photography": ("photo", "photography", "instagram", "scenic"),
        "architecture": ("architecture", "building", "cathedral", "design"),
        "culture": ("culture", "museum", "local", "tradition"),
        "sports": ("sport", "stadium", "match", "game"),
        "music": ("music", "concert", "live music", "jazz"),
    }
    interests = [
        tag
        for tag, keywords in interest_keywords.items()
        if any(keyword in lowered for keyword in keywords)
    ]

    if any(word in lowered for word in ("packed", "as much as", "maximise", "maximize")):
        pace = "packed"
    elif any(word in lowered for word in ("relaxed", "slow", "easy", "leisurely")):
        pace = "relaxed"
    else:
        pace = "moderate"

    if any(word in lowered for word in ("luxury", "high-end", "splurge")):
        budget = "luxury"
    elif any(word in lowered for word in ("cheap", "budget", "backpack", "shoestring")):
        budget = "budget"
    else:
        budget = "moderate"

    return {
        "interests": interests or ["culture"],
        "pace": pace,
        "budget_level": budget,
        "keywords": sorted(set(re.findall(r"[a-z]{4,}", lowered)))[:12],
        "notes": "Parsed by the deterministic stub provider.",
    }


def _stub_selection(prompt: str, digest: str) -> dict[str, Any]:
    """Rank the candidate ids the prompt listed, deterministically."""
    ids = re.findall(r"id=([0-9a-fA-F]{24})", prompt)
    seed = int(digest[:8], 16)
    selections = []
    for position, place_id in enumerate(ids):
        # Stable pseudo-score in [0.35, 0.95], derived from the id itself.
        bucket = (seed + int(place_id[-6:], 16)) % 61
        selections.append(
            {
                "place_id": place_id,
                "score": round(0.35 + bucket / 100.0, 3),
                "reason": f"Matches the stated interests (rank {position + 1}).",
            }
        )
    selections.sort(key=lambda item: item["score"], reverse=True)
    return {"selections": selections}


def _stub_narration(prompt: str, digest: str) -> dict[str, Any]:
    days = re.findall(r"day_number=(\d+)", prompt)
    return {
        "title": "Your planned trip",
        "summary": (
            "A balanced plan that groups nearby places together to keep travel "
            "time low and leaves room to slow down."
        ),
        "days": [
            {
                "day_number": int(day),
                "title": f"Day {day}",
                "summary": "Places chosen to sit close together so the day flows.",
            }
            for day in days
        ],
    }


# ---------------------------------------------------------------------------
# Real providers
# ---------------------------------------------------------------------------


class AnthropicProvider(BaseLLMProvider):
    name = "anthropic"

    def __init__(self) -> None:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:  # pragma: no cover
            raise ExternalServiceError(
                "The anthropic SDK is not installed; run "
                "`pip install -r requirements-ai.txt`."
            ) from exc
        if not settings.anthropic_api_key:
            raise ExternalServiceError("ANTHROPIC_API_KEY is not configured.")
        self._client = AsyncAnthropic(
            api_key=settings.anthropic_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
        )

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        started = time.perf_counter()
        kwargs: dict[str, Any] = {
            "model": settings.resolved_llm_model,
            "max_tokens": max_tokens or settings.llm_max_tokens,
            "temperature": (
                settings.llm_temperature if temperature is None else temperature
            ),
            "messages": [{"role": m.role, "content": m.content} for m in messages],
        }
        if system:
            kwargs["system"] = system
        if tools:
            kwargs["tools"] = tools

        try:
            response = await self._client.messages.create(**kwargs)
        except Exception as exc:  # pragma: no cover - network
            LLM_CALLS.labels(self.name, "error").inc()
            raise ExternalServiceError(f"Anthropic request failed: {exc}") from exc

        text = "".join(
            block.text for block in response.content if getattr(block, "type", "") == "text"
        )
        tool_calls = [
            {"name": block.name, "input": block.input}
            for block in response.content
            if getattr(block, "type", "") == "tool_use"
        ]
        return self._finish(
            text,
            tool_calls,
            response.usage.input_tokens,
            response.usage.output_tokens,
            started,
            settings.resolved_llm_model,
        )

    def _finish(
        self,
        text: str,
        tool_calls: list[dict[str, Any]],
        prompt_tokens: int,
        completion_tokens: int,
        started: float,
        model: str,
    ) -> LLMResponse:
        LLM_CALLS.labels(self.name, "success").inc()
        LLM_TOKENS.labels(self.name, "prompt").inc(prompt_tokens)
        LLM_TOKENS.labels(self.name, "completion").inc(completion_tokens)
        return LLMResponse(
            text=text,
            provider=self.name,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
            tool_calls=tool_calls,
        )


class OpenAIProvider(BaseLLMProvider):
    name = "openai"

    def __init__(self) -> None:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:  # pragma: no cover
            raise ExternalServiceError(
                "The openai SDK is not installed; run `pip install -r requirements-ai.txt`."
            ) from exc
        if not settings.openai_api_key:
            raise ExternalServiceError("OPENAI_API_KEY is not configured.")
        if not settings.llm_model:
            raise ExternalServiceError(
                "LLM_MODEL must be set explicitly when LLM_PROVIDER=openai."
            )
        self._client = AsyncOpenAI(
            api_key=settings.openai_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
        )

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        started = time.perf_counter()
        payload = [{"role": "system", "content": system}] if system else []
        payload += [{"role": m.role, "content": m.content} for m in messages]

        try:
            response = await self._client.chat.completions.create(
                model=settings.resolved_llm_model,
                messages=payload,  # type: ignore[arg-type]
                max_tokens=max_tokens or settings.llm_max_tokens,
                temperature=(
                    settings.llm_temperature if temperature is None else temperature
                ),
            )
        except Exception as exc:  # pragma: no cover - network
            LLM_CALLS.labels(self.name, "error").inc()
            raise ExternalServiceError(f"OpenAI request failed: {exc}") from exc

        usage = response.usage
        LLM_CALLS.labels(self.name, "success").inc()
        return LLMResponse(
            text=response.choices[0].message.content or "",
            provider=self.name,
            model=settings.resolved_llm_model,
            prompt_tokens=getattr(usage, "prompt_tokens", 0),
            completion_tokens=getattr(usage, "completion_tokens", 0),
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


class GeminiProvider(BaseLLMProvider):
    name = "gemini"

    def __init__(self) -> None:
        try:
            from google import genai
        except ImportError as exc:  # pragma: no cover
            raise ExternalServiceError(
                "The google-genai SDK is not installed; run "
                "`pip install -r requirements-ai.txt`."
            ) from exc
        if not settings.google_gemini_key:
            raise ExternalServiceError("GOOGLE_GEMINI_KEY is not configured.")
        self._client = genai.Client(api_key=settings.google_gemini_key.get_secret_value())

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        started = time.perf_counter()
        prompt = "\n\n".join(m.content for m in messages)
        try:
            response = await self._client.aio.models.generate_content(
                model=settings.resolved_llm_model,
                contents=prompt,
                config={
                    "system_instruction": system,
                    "max_output_tokens": max_tokens or settings.llm_max_tokens,
                    "temperature": (
                        settings.llm_temperature if temperature is None else temperature
                    ),
                },
            )
        except Exception as exc:  # pragma: no cover - network
            LLM_CALLS.labels(self.name, "error").inc()
            raise ExternalServiceError(f"Gemini request failed: {exc}") from exc

        usage = getattr(response, "usage_metadata", None)
        prompt_tokens = getattr(usage, "prompt_token_count", 0) or 0
        completion_tokens = getattr(usage, "candidates_token_count", 0) or 0
        LLM_CALLS.labels(self.name, "success").inc()
        LLM_TOKENS.labels(self.name, "prompt").inc(prompt_tokens)
        LLM_TOKENS.labels(self.name, "completion").inc(completion_tokens)
        return LLMResponse(
            text=response.text or "",
            provider=self.name,
            model=settings.resolved_llm_model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )


class GroqProvider(BaseLLMProvider):
    """Groq's hosted inference, through the official ``groq`` SDK.

    The model always comes from the environment (``GROQ_MODEL``): Groq retires
    models often enough that any ID written here would go stale. Every call
    has a timeout and the SDK's bounded retries with exponential backoff on
    timeouts, connection errors, 429 and 5xx, honouring ``Retry-After``. What
    still fails after that is reported for what it is - a rate limit as 429
    with how long to wait, never as a generic failure.
    """

    name = "groq"

    def __init__(self) -> None:
        try:
            import groq
        except ImportError as exc:  # pragma: no cover
            raise ExternalServiceError(
                "The groq SDK is not installed; run `pip install -r requirements-ai.txt`."
            ) from exc
        if not settings.groq_api_key:
            raise ExternalServiceError("GROQ_API_KEY is not configured.")
        if not settings.resolved_llm_model:
            raise ExternalServiceError(
                "GROQ_MODEL is not configured; pick a model from "
                "https://console.groq.com/docs/models."
            )
        self._sdk = groq
        self._client = groq.AsyncGroq(
            api_key=settings.groq_api_key.get_secret_value(),
            timeout=settings.llm_timeout_seconds,
            max_retries=settings.llm_max_retries,
        )

    async def complete(
        self,
        messages: list[LLMMessage],
        *,
        system: str | None = None,
        tools: list[dict[str, Any]] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> LLMResponse:
        started = time.perf_counter()
        model = settings.resolved_llm_model
        payload = [{"role": "system", "content": system}] if system else []
        payload += [{"role": m.role, "content": m.content} for m in messages]
        extra: dict[str, Any] = {}
        if json_mode:
            # Groq requires the word "JSON" in the prompt for this mode; the
            # schema instruction from complete_json always contains it.
            extra["response_format"] = {"type": "json_object"}

        try:
            response = await self._client.chat.completions.create(
                model=model,
                messages=payload,  # type: ignore[arg-type]
                max_completion_tokens=max_tokens or settings.llm_max_tokens,
                temperature=(
                    settings.llm_temperature if temperature is None else temperature
                ),
                **extra,
            )
        except Exception as exc:
            LLM_CALLS.labels(self.name, "error").inc()
            raise self._translate(exc, model, started) from exc

        usage = response.usage
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        LLM_CALLS.labels(self.name, "success").inc()
        LLM_TOKENS.labels(self.name, "prompt").inc(prompt_tokens)
        LLM_TOKENS.labels(self.name, "completion").inc(completion_tokens)
        return LLMResponse(
            text=response.choices[0].message.content or "",
            provider=self.name,
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    async def _complete_for_json(
        self,
        messages: list[LLMMessage],
        *,
        system: str,
        max_tokens: int | None,
    ) -> LLMResponse:
        return await self.complete(
            messages, system=system, max_tokens=max_tokens, json_mode=True
        )

    def _translate(self, exc: Exception, model: str, started: float) -> Exception:
        """Turn an SDK error into one that tells the user what happened.

        Logged with the model, status and latency only: never the prompt (it
        holds the traveller's plans) or the key.
        """
        status_code = getattr(exc, "status_code", None)
        logger.warning(
            "llm_call_failed",
            provider=self.name,
            model=model,
            status=status_code,
            error_type=type(exc).__name__,
            elapsed_ms=int((time.perf_counter() - started) * 1000),
        )

        rate_limited = getattr(self._sdk, "RateLimitError", None)
        is_rate_limit = rate_limited is not None and isinstance(exc, rate_limited)
        if is_rate_limit or status_code == 429:
            retry_after = _retry_after_seconds(exc)
            wait = f"about {retry_after} seconds" if retry_after else "a minute"
            return RateLimitError(
                "The AI provider (Groq) is rate-limiting requests right now. "
                f"Try again in {wait}.",
                details={"provider": self.name, "retry_after_seconds": retry_after},
            )

        timed_out = getattr(self._sdk, "APITimeoutError", None)
        if timed_out is not None and isinstance(exc, timed_out):
            return ExternalServiceError(
                f"Groq did not answer within {settings.llm_timeout_seconds} seconds.",
                details={"provider": self.name},
            )

        if status_code is not None:
            return ExternalServiceError(
                f"Groq returned an error (HTTP {status_code}).",
                details={"provider": self.name, "status": status_code},
            )
        return ExternalServiceError(
            "Could not reach Groq.", details={"provider": self.name}
        )


def _retry_after_seconds(exc: Exception) -> int | None:
    """The provider's ``Retry-After`` hint, in whole seconds, when it sent one."""
    headers = getattr(getattr(exc, "response", None), "headers", None)
    value = headers.get("retry-after") if headers is not None else None
    try:
        return max(1, round(float(value))) if value is not None else None
    except (TypeError, ValueError):
        return None


_PROVIDERS: dict[LLMProvider, type[BaseLLMProvider]] = {
    LLMProvider.STUB: StubProvider,
    LLMProvider.ANTHROPIC: AnthropicProvider,
    LLMProvider.OPENAI: OpenAIProvider,
    LLMProvider.GEMINI: GeminiProvider,
    LLMProvider.GROQ: GroqProvider,
}

_instance: BaseLLMProvider | None = None


def get_llm_provider(force: LLMProvider | None = None) -> BaseLLMProvider:
    """Return the configured provider, falling back to the stub on failure."""
    global _instance
    if force is not None:
        return _PROVIDERS[force]()
    if _instance is not None:
        return _instance

    provider_cls = _PROVIDERS[settings.llm_provider]
    try:
        _instance = provider_cls()
    except ExternalServiceError as exc:
        logger.warning(
            "llm_provider_unavailable",
            provider=str(settings.llm_provider),
            error=str(exc),
            falling_back_to="stub",
        )
        _instance = StubProvider()
    return _instance


def reset_llm_provider() -> None:
    """Clear the cached provider. Used by tests that swap configuration."""
    global _instance
    _instance = None
