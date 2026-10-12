"""Optional OpenTelemetry and Sentry wiring.

Both are no-ops unless explicitly enabled, so that local development and the test
suite never depend on the exporter packages being installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import settings
from app.observability.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI

logger = get_logger(__name__)


def setup_sentry() -> None:
    if not settings.sentry_dsn:
        return
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("sentry_sdk_missing", hint="pip install sentry-sdk")
        return
    sentry_sdk.init(
        dsn=settings.sentry_dsn,
        environment=str(settings.environment),
        traces_sample_rate=0.1 if settings.is_production else 1.0,
        send_default_pii=False,
    )
    logger.info("sentry_initialised", environment=str(settings.environment))


def setup_tracing(app: FastAPI) -> None:
    if not settings.otel_enabled:
        return
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        logger.warning(
            "otel_missing",
            hint="pip install opentelemetry-sdk opentelemetry-instrumentation-fastapi "
            "opentelemetry-exporter-otlp-proto-http",
        )
        return

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.app_name,
                "deployment.environment": str(settings.environment),
            }
        )
    )
    if settings.otel_exporter_otlp_endpoint:
        provider.add_span_processor(
            BatchSpanProcessor(
                OTLPSpanExporter(endpoint=settings.otel_exporter_otlp_endpoint)
            )
        )
    trace.set_tracer_provider(provider)
    FastAPIInstrumentor.instrument_app(app)
    logger.info("otel_initialised", endpoint=settings.otel_exporter_otlp_endpoint)
