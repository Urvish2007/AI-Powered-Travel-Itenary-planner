"""Application settings, loaded from the environment via pydantic-settings."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import (
    AliasChoices,
    Field,
    RedisDsn,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# A fixed, obviously-fake key: long enough to satisfy HS256, recognisable enough
# that the production guard below can refuse to start on it.
DEV_SECRET_KEY = "dev-only-insecure-secret-key-change-me-in-every-real-environment"


class Environment(StrEnum):
    LOCAL = "local"
    TEST = "test"
    STAGING = "staging"
    PRODUCTION = "production"


class LLMProvider(StrEnum):
    """Which LLM backend the AI layer talks to.

    ``STUB`` is a deterministic, dependency-free provider used by tests and by
    local development when no API key is configured. Every other provider
    requires its matching SDK and API key.
    """

    STUB = "stub"
    ANTHROPIC = "anthropic"
    OPENAI = "openai"
    GEMINI = "gemini"
    GROQ = "groq"


class OptimizerAlgorithm(StrEnum):
    """Route optimisation algorithms. See ``app.optimization.algorithms``.

    Defined here (like ``LLMProvider``) so settings can use it without importing
    the models package, which itself imports settings.
    """

    AUTO = "auto"
    ORTOOLS = "ortools"
    NEAREST_NEIGHBOR = "nearest_neighbor"
    CHEAPEST_INSERTION = "cheapest_insertion"
    LOCAL_SEARCH = "local_search"
    SIMULATED_ANNEALING = "simulated_annealing"
    GENETIC = "genetic"


class EmbeddingProviderName(StrEnum):
    """Where place and query embeddings come from. Independent of the LLM.

    ``HASH`` is a deterministic lexical embedder: no key, no network, and the
    only option that works with nothing but a Groq key (Groq has no embeddings
    API). The others are learned embeddings; see ``app.ai.embeddings``.
    """

    HASH = "hash"
    OPENAI = "openai"
    GEMINI = "gemini"
    LOCAL = "local"


class RoutingProviderName(StrEnum):
    """Where travel distances and durations come from.

    ``HAVERSINE`` is the straight-line estimate: no network, never fails, and
    what every plan used before routing providers existed. The others measure
    the road network; see ``app.services.routing_service``.
    """

    HAVERSINE = "haversine"
    OPENROUTESERVICE = "openrouteservice"
    OSRM = "osrm"


# Model IDs used when LLM_MODEL / LLM_FAST_MODEL are not set. Providers retire
# models on a schedule (Gemini 2.5 and Groq's Llama 3.x lines are already on it),
# so these are deliberately overridable from the environment rather than baked
# into the code paths. OpenAI has no default: pick one deliberately.
DEFAULT_MODELS: dict[LLMProvider, tuple[str, str]] = {
    # (main model, fast model)
    LLMProvider.STUB: ("stub", "stub"),
    LLMProvider.GEMINI: ("gemini-3.8-flash", "gemini-3.5-flash-lite"),
    # Groq has no default on purpose: its catalogue changes often enough that a
    # baked-in ID goes stale. Set GROQ_MODEL (see Settings.groq_model).
    LLMProvider.ANTHROPIC: ("claude-sonnet-5-5", "claude-haiku-4-5-20251001"),
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", ".env.local"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        populate_by_name=True,
    )

    # ---- App ----
    app_name: str = "AI Itinerary Planner API"
    environment: Environment = Environment.LOCAL
    debug: bool = True
    api_v1_prefix: str = "/api/v1"
    # Comma-separated in the environment, e.g. CORS_ORIGINS=http://localhost:3000
    # NoDecode stops pydantic-settings JSON-decoding the value before
    # ``_split_origins`` below gets a chance to split it on commas.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # ---- MongoDB ----
    # Plain str, NOT MongoDsn: Pydantic's MongoDsn injects :27017 into every
    # URI, which breaks mongodb+srv:// (SRV URIs must not contain a port).
    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_db_name: str = "itinerary_planner"
    # Atlas Vector Search is unavailable on a plain self-hosted mongod; when this is
    # False the retrieval layer falls back to in-process cosine similarity.
    mongodb_vector_search_enabled: bool = False
    mongodb_vector_index_name: str = "place_embedding_index"

    # ---- Redis ----
    redis_uri: RedisDsn = Field(default=RedisDsn("redis://localhost:6379/0"))
    redis_enabled: bool = False
    cache_ttl_seconds: int = 300

    # ---- Celery ----
    celery_broker_url: str = "redis://localhost:6379/1"
    celery_result_backend: str = "redis://localhost:6379/2"
    celery_task_always_eager: bool = False

    # ---- Auth ----
    # 32+ bytes, as RFC 7518 requires for HS256. The default is safe only for
    # local development; ``_reject_default_secret`` blocks it outside of it.
    secret_key: SecretStr = SecretStr(DEV_SECRET_KEY)
    jwt_algorithm: Literal["HS256", "HS512"] = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 30

    # ---- Federated sign-in (Google / Apple) ----
    # These are OAuth *client ids*, not secrets: the browser completes sign-in and
    # sends us a signed ID token, which the server verifies against the
    # provider's public keys. The client id is the expected `aud` claim.
    # Comma-separate several when one project has web + iOS + Android clients.
    google_client_id: str | None = None
    # Apple calls this the Services ID for web, or the bundle id for native apps.
    apple_client_id: str | None = None

    # ---- AI ----
    llm_provider: LLMProvider = LLMProvider.STUB
    # ``None`` means "use the provider's default from DEFAULT_MODELS".
    llm_model: str | None = None
    llm_fast_model: str | None = None
    llm_max_tokens: int = 8192
    llm_temperature: float = 0.4
    llm_timeout_seconds: int = 120
    # Gemini reads GOOGLE_GEMINI_KEY; the older GEMINI_API_KEY is still accepted
    # so an existing .env keeps working.
    google_gemini_key: SecretStr | None = Field(
        default=None,
        validation_alias=AliasChoices("GOOGLE_GEMINI_KEY", "GEMINI_API_KEY"),
    )
    groq_api_key: SecretStr | None = None
    # Groq models come from the environment, never from code. GROQ_MODEL wins
    # over LLM_MODEL when LLM_PROVIDER=groq, so a stale LLM_MODEL left over from
    # another provider is never sent to Groq. The fast model falls back to the
    # main one rather than to a guess.
    groq_model: str | None = None
    groq_fast_model: str | None = None
    # Retries for transient provider failures (timeouts, connection errors, 429
    # and 5xx), with exponential backoff that honours Retry-After.
    llm_max_retries: int = 2
    anthropic_api_key: SecretStr | None = None
    openai_api_key: SecretStr | None = None
    # ---- Embeddings ----
    # hash (default) | openai | gemini | local. Every stored vector records the
    # provider, model and dimension that made it, and search only compares
    # vectors from the configured one - so changing this needs a re-embed
    # (`python -m scripts.reembed`) before semantic search finds anything.
    embedding_provider: EmbeddingProviderName = EmbeddingProviderName.HASH
    # Required for openai / gemini / local; ignored by hash.
    embedding_model: str | None = None
    embedding_dimensions: int = 384
    embedding_batch_size: int = 64
    embedding_timeout_seconds: float = 30.0
    # Retries on timeouts, 429 and 5xx, with exponential backoff.
    embedding_max_retries: int = 3

    # ---- Optimization ----
    # Wall-clock ceiling handed to the OR-Tools solver per optimisation request.
    optimizer_time_limit_seconds: int = 10
    # Which algorithm plans itineraries unless a request asks for another:
    # auto | ortools | nearest_neighbor | cheapest_insertion | local_search |
    # simulated_annealing | genetic. "ortools" keeps the long-standing behaviour.
    optimizer_algorithm: OptimizerAlgorithm = OptimizerAlgorithm.ORTOOLS
    # Straight-line distance is scaled by this factor to approximate road distance.
    road_distance_factor: float = 1.3
    default_travel_speed_kmh: float = 30.0

    # ---- Road routing ----
    # haversine (estimate, the default) | openrouteservice | osrm. Any failure
    # falls back to the estimate and responses say so ("estimated": true).
    routing_provider: RoutingProviderName = RoutingProviderName.HAVERSINE
    # openrouteservice API key. Not needed for a self-hosted ORS or for OSRM.
    routing_api_key: SecretStr | None = None
    # Provider root URL. ORS defaults to https://api.openrouteservice.org; OSRM
    # has no default, because the public demo server is not for production.
    routing_base_url: str | None = None
    routing_timeout_seconds: float = 4.0
    # Retries apply to timeouts, connection errors, 429 and 5xx only.
    routing_max_retries: int = 2
    routing_retry_backoff_seconds: float = 0.5
    # More points than this use the estimate: ORS's free matrix allows 3,500
    # cells (59 points), and generation can weigh hundreds of candidates.
    routing_max_locations: int = 50
    routing_cache_ttl_seconds: int = 86_400
    routing_cache_max_entries: int = 512
    # Consecutive failures that open the breaker, and how long it stays open.
    routing_breaker_threshold: int = 3
    routing_breaker_cooldown_seconds: int = 300

    # ---- City photos (scripts/fetch_city_photos.py) ----
    # Wikimedia asks every client to identify itself and how to reach its
    # maintainer: https://meta.wikimedia.org/wiki/User-Agent_policy
    photos_user_agent: str = (
        "AI-Itinerary-Planner/1.0 "
        "(https://github.com/Urvish2007/AI-Powered-Travel-Itenary-planner)"
    )
    # Pause before every request: one at a time, well inside Wikimedia's limits.
    photos_request_interval_seconds: float = 0.5
    photos_timeout_seconds: float = 15.0
    photos_max_retries: int = 3

    # ---- Observability ----
    sentry_dsn: str | None = None
    log_level: str = "INFO"
    log_json: bool = False
    metrics_enabled: bool = True
    otel_enabled: bool = False
    otel_exporter_otlp_endpoint: str | None = None

    # ---- Rate limiting ----
    rate_limit_enabled: bool = True
    rate_limit_requests: int = 120
    rate_limit_window_seconds: int = 60

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _check_embedding_provider(self) -> Settings:
        """A learned-embedding provider must say which model, and have its key.

        Checked in every environment: unlike the LLM there is no sensible
        fallback, because a silent switch to another embedder would mix vector
        spaces and make every similarity meaningless.
        """
        provider = self.embedding_provider
        if provider is EmbeddingProviderName.HASH:
            return self
        if not self.embedding_model:
            raise ValueError(f"EMBEDDING_PROVIDER={provider} needs EMBEDDING_MODEL.")
        if provider is EmbeddingProviderName.OPENAI and self.openai_api_key is None:
            raise ValueError("EMBEDDING_PROVIDER=openai needs OPENAI_API_KEY.")
        if provider is EmbeddingProviderName.GEMINI and self.google_gemini_key is None:
            raise ValueError("EMBEDDING_PROVIDER=gemini needs GOOGLE_GEMINI_KEY.")
        return self

    @model_validator(mode="after")
    def _reject_default_secret(self) -> Settings:
        """Refuse to boot a deployed environment on the development key."""
        if self.environment in (Environment.STAGING, Environment.PRODUCTION):
            if self.secret_key.get_secret_value() == DEV_SECRET_KEY:
                raise ValueError(
                    "SECRET_KEY is still the development default; set a unique "
                    f"value before running in {self.environment}."
                )
            if len(self.secret_key.get_secret_value().encode()) < 32:
                raise ValueError("SECRET_KEY must be at least 32 bytes for HS256.")
            # Locally a misconfigured provider falls back to the stub, which is
            # convenient. Deployed, that would serve canned itineraries as if
            # they were real, so it is refused instead.
            if self.llm_provider is LLMProvider.GROQ:
                if self.groq_api_key is None:
                    raise ValueError("LLM_PROVIDER=groq needs GROQ_API_KEY.")
                if not self.resolved_llm_model:
                    raise ValueError(
                        "LLM_PROVIDER=groq needs GROQ_MODEL; pick one from "
                        "https://console.groq.com/docs/models."
                    )
        return self

    @property
    def resolved_llm_model(self) -> str:
        """The model the active provider should call; empty when none is set."""
        if self.llm_provider is LLMProvider.GROQ and self.groq_model:
            return self.groq_model
        if self.llm_model:
            return self.llm_model
        return DEFAULT_MODELS.get(self.llm_provider, ("", ""))[0]

    @property
    def resolved_llm_fast_model(self) -> str:
        """The cheaper/faster model for lightweight calls."""
        if self.llm_provider is LLMProvider.GROQ:
            return self.groq_fast_model or self.llm_fast_model or self.resolved_llm_model
        if self.llm_fast_model:
            return self.llm_fast_model
        return DEFAULT_MODELS.get(self.llm_provider, ("", ""))[1]

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def is_testing(self) -> bool:
        return self.environment is Environment.TEST

    @property
    def mongodb_uri_str(self) -> str:
        return self.mongodb_uri


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()


settings = get_settings()
