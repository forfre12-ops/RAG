"""P1-F5: OpenTelemetry 부트스트랩 — FastAPI / SQLAlchemy / Redis / httpx 트레이싱.

운영 활성:
  pip install -e ".[otel]"
  export OTEL_EXPORTER_OTLP_ENDPOINT=http://tempo:4317
  export OTEL_SERVICE_NAME=koipa-api

코드:
  from koipa.obs.otel import setup_tracing
  setup_tracing(app)

dryrun/CI에서는 OTel 패키지 미설치라 setup_tracing() no-op.
"""

from __future__ import annotations

import contextlib
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

    svc = service_name or os.getenv("OTEL_SERVICE_NAME", "koipa-api")
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
    # #30: Celery(워커 task trace 전파) / Logging(로그에 trace_id/span_id 주입) 자동계측 추가.
    #      미설치 시 아래 try/except가 흡수 → silent skip (기존 자동계측 패턴 그대로).
    for mod, label in (
        ("opentelemetry.instrumentation.sqlalchemy", "SQLAlchemyInstrumentor"),
        ("opentelemetry.instrumentation.redis", "RedisInstrumentor"),
        ("opentelemetry.instrumentation.httpx", "HTTPXClientInstrumentor"),
        ("opentelemetry.instrumentation.celery", "CeleryInstrumentor"),
        ("opentelemetry.instrumentation.logging", "LoggingInstrumentor"),
    ):
        try:
            m = __import__(mod, fromlist=[label])
            getattr(m, label)().instrument()
        except Exception as e:  # noqa: BLE001
            logger.debug("instrumentation %s skipped: %s", label, e)

    logger.info("OpenTelemetry tracing enabled: service=%s endpoint=%s", svc, endpoint)
    return True


# ──────────────────────────────────────────────────────────────────────────
# #29: 비즈니스(수동) span 헬퍼 — ML 추론 내부를 가시화.
# 자동계측(FastAPI/SQLAlchemy/Redis/httpx)만으로는 classify()·retrieval 내부가
# 블랙박스다. 핵심 경로를 `with span("..."):`로 보수적으로 감싸 트레이스에 노출한다.
# OTel 미설치/미활성(provider 미설정)이면 **완전 no-op** — 예외 없이 통과.
# ──────────────────────────────────────────────────────────────────────────
@contextlib.contextmanager
def span(name: str, **attrs):
    """경량 수동 span 컨텍스트매니저.

    - OTel 미설치 또는 tracer-provider 미설정이면 아무 것도 안 하고 통과(no-op).
    - 활성 시 tracer.start_as_current_span(name)으로 span을 만들고, attrs를
      set_attribute로 부착한다(None 값은 제외 — 비어있는 속성 노이즈 방지).
    - 민감정보(문서 본문 등)는 호출부에서 절대 넘기지 않는다(식별자/등급/신뢰도 위주).

    어떤 단계에서 예외가 나도 span 부재로 폴백 — 비즈니스 로직을 깨지 않는다.
    """
    try:
        from opentelemetry import trace  # type: ignore  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        yield None
        return

    try:
        tracer = trace.get_tracer("koipa")
    except Exception:  # noqa: BLE001
        yield None
        return

    try:
        cm = tracer.start_as_current_span(name)
    except Exception:  # noqa: BLE001
        yield None
        return

    with cm as sp:
        # set_attribute는 None/복합타입에 민감 — 스칼라만, None 제외로 방어.
        if sp is not None:
            for k, v in attrs.items():
                if v is None:
                    continue
                try:
                    sp.set_attribute(k, v)
                except Exception:  # noqa: BLE001
                    pass
        yield sp


# ──────────────────────────────────────────────────────────────────────────
# #30: 구조화(JSON) 로깅 — opt-in.
# 환경변수 KOIPA_LOG_JSON이 truthy일 때만 root logger에 JSON 포매터를 적용한다.
# 기본(미설정)은 **아무 변경 없음** — 기존 로깅·테스트 로그 출력 보존.
# trace_id/span_id는 LoggingInstrumentor가 활성일 때 LogRecord에 주입되며,
# 미주입(미설치) 환경에서도 getattr 폴백으로 안전하게 동작한다.
# ──────────────────────────────────────────────────────────────────────────
def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def setup_logging() -> bool:
    """opt-in JSON 구조화 로깅 설정. KOIPA_LOG_JSON truthy일 때만 적용.

    Returns:
        True if JSON logging applied, False if no-op (기본).
    """
    if not _truthy(os.getenv("KOIPA_LOG_JSON")):
        # 기본 경로: 아무 것도 바꾸지 않는다(기존 동작·테스트 보존).
        return False

    import json  # noqa: PLC0415

    class _JsonFormatter(logging.Formatter):
        def format(self, record: logging.LogRecord) -> str:
            payload = {
                "ts": self.formatTime(record),
                "level": record.levelname,
                "logger": record.name,
                "msg": record.getMessage(),
            }
            # LoggingInstrumentor가 주입한 trace 컨텍스트(있을 때만).
            for fld in ("otelTraceID", "otelSpanID"):
                val = getattr(record, fld, None)
                if val:
                    payload["trace_id" if fld == "otelTraceID" else "span_id"] = val
            # 도메인 식별자 — extra로 들어온 경우만(있을 때만).
            # tenant 제거: 단일 고객사 엔진(격리는 KL 포털 전담), tenant_id 로깅 없음.
            for fld in ("job_id",):
                val = getattr(record, fld, None)
                if val is not None:
                    payload[fld] = val
            if record.exc_info:
                payload["exc"] = self.formatException(record.exc_info)
            return json.dumps(payload, ensure_ascii=False)

    root = logging.getLogger()
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    # 기존 핸들러를 교체(중복 출력 방지). 레벨은 기존 root 레벨 보존.
    root.handlers = [handler]
    logger.info("JSON structured logging enabled (KOIPA_LOG_JSON)")
    return True
