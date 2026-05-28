"""P1-F5: OpenTelemetry 부트스트랩 — FastAPI / SQLAlchemy / Redis / httpx 트레이싱.

운영 활성:
  pip install -e ".[otel]"
  export OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317
  export OTEL_SERVICE_NAME=lloydk-api

코드:
  from lloydk.obs.otel import setup_tracing
  setup_tracing(app)

dryrun/CI에서는 OTel 패키지 미설치라 setup_tracing() no-op.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)


def setup_tracing(app=None, *, service_name: str | None = None) -> bool:
    """OpenTelemetry tracing 활성. 의존성 없으면 silent skip.

    Args:
        app: FastAPI 앱 (FastAPIInstrumentor 적용용)
        service_name: 서비스 이름 (기본 env OTEL_SERVICE_NAME)

    Returns:
        True if instrumented, False if skipped.
    """
    try:
        from opentelemetry import trace  # type: ignore
        from opentelemetry.sdk.resources import Resource  # type: ignore
        from opentelemetry.sdk.trace import TracerProvider  # type: ignore
        from opentelemetry.sdk.trace.export import BatchSpanProcessor  # type: ignore
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter  # type: ignore
    except Exception:
        logger.info("OpenTelemetry deps not installed — tracing skipped")
        return False

    svc = service_name or os.getenv("OTEL_SERVICE_NAME", "lloydk-api")
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    if not endpoint:
        logger.info("OTEL_EXPORTER_OTLP_ENDPOINT not set — tracing skipped")
        return False

    resource = Resource.create({"service.name": svc, "service.version": "0.1.0-poc"})
    provider = TracerProvider(resource=resource)
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # FastAPI 자동 계측
    if app is not None:
        try:
            from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor  # type: ignore
            FastAPIInstrumentor.instrument_app(app)
        except Exception as e:  # noqa: BLE001
            logger.warning("FastAPIInstrumentor failed: %s", e)

    # SQLAlchemy / Redis / httpx
    for mod, label in (
        ("opentelemetry.instrumentation.sqlalchemy", "SQLAlchemyInstrumentor"),
        ("opentelemetry.instrumentation.redis", "RedisInstrumentor"),
        ("opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"),
    ):
        try:
            m = __import__(mod, fromlist=[label])
            getattr(m, label)().instrument()
        except Exception as e:  # noqa: BLE001
            logger.debug("instrumentation %s skipped: %s", label, e)

    logger.info("OpenTelemetry tracing enabled: service=%s endpoint=%s", svc, endpoint)
    return True
