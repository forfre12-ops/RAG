"""Prometheus instrumentation — FastAPI 미들웨어 + /metrics-prom 엔드포인트.

설계:
- PrometheusMiddleware: 요청수·지연·in-progress·에러를 자동 수집
- /api/v1/metrics-prom: prometheus 표준 exposition (text/plain)
- /healthz·/metrics-prom 자체는 스크랩 제외 (셀프 카운트 회피)
- route 라벨은 path template (예: /api/v1/classify/{doc_id}) — 카디널리티 폭증 방지
- active_learning 게이지는 백그라운드 refresh 없이 호출 시점에 lazy 갱신
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable

from fastapi import APIRouter, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware

# 자체 레지스트리 — 테스트 격리 + 다중 import 시 중복 등록 방지.
registry = CollectorRegistry()

REQUEST_COUNT = Counter(
    "lloydk_requests_total",
    "Total HTTP requests received",
    ["method", "route", "status"],
    registry=registry,
)

REQUEST_LATENCY = Histogram(
    "lloydk_request_duration_seconds",
    "Request latency in seconds",
    ["method", "route"],
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0),
    registry=registry,
)

INPROGRESS = Gauge(
    "lloydk_inprogress_requests",
    "In-progress HTTP requests",
    ["method", "route"],
    registry=registry,
)

EXCEPTIONS = Counter(
    "lloydk_request_exceptions_total",
    "Unhandled exceptions raised by route handlers",
    ["method", "route", "type"],
    registry=registry,
)

# 비즈니스 지표
ACTIVE_LEARNING_TOTAL = Gauge(
    "lloydk_active_learning_pending_total",
    "Total unconsumed corrections in queue",
    registry=registry,
)
ACTIVE_LEARNING_UNDERCLASS = Gauge(
    "lloydk_active_learning_pending_underclass",
    "Unconsumed underclass corrections (security misses)",
    registry=registry,
)

# 스크랩 제외 경로 — 셀프 카운트 회피 + 노이즈 차단
_EXCLUDED = {
    "/api/v1/metrics-prom",
    "/api/v1/healthz",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/api/v1/openapi.json",
}


def _route_template(request: Request) -> str:
    """카디널리티 안정 — path template 우선, 없으면 raw path."""
    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        return route.path
    return request.url.path


class PrometheusMiddleware(BaseHTTPMiddleware):
    """요청별 메트릭 자동 수집. AuditMiddleware보다 안쪽에 두면 안 됨 (라우팅 끝나야 route_template 확보)."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path in _EXCLUDED:
            return await call_next(request)

        method = request.method
        # route 템플릿은 dispatch 종료 후에야 결정됨 — try/finally로 measure
        start = time.perf_counter()
        route_label = path  # 폴백 (라우팅 실패 시)

        try:
            response = await call_next(request)
            route_label = _route_template(request)
            INPROGRESS.labels(method=method, route=route_label).inc()
            status = str(response.status_code)
            REQUEST_COUNT.labels(method=method, route=route_label, status=status).inc()
            return response
        except Exception as exc:  # noqa: BLE001
            route_label = _route_template(request)
            EXCEPTIONS.labels(method=method, route=route_label, type=type(exc).__name__).inc()
            REQUEST_COUNT.labels(method=method, route=route_label, status="500").inc()
            raise
        finally:
            elapsed = time.perf_counter() - start
            REQUEST_LATENCY.labels(method=method, route=route_label).observe(elapsed)
            # inprogress는 dispatch 진입 시 inc 못 했지만 (route_label 미정)
            # gauge 음수 방지 위해 매번 0 set 대신 best-effort dec
            try:
                INPROGRESS.labels(method=method, route=route_label).dec()
            except Exception:  # noqa: BLE001
                pass


# ============================================================
# /metrics-prom router
# ============================================================

router = APIRouter()


def _refresh_business_gauges() -> None:
    """active_learning gauge를 호출 시점에 lazy 갱신.

    실패해도 메트릭 노출 자체는 중단되지 않게 best-effort.
    """
    try:
        from lloydk.modules.m6_evaluation.active_learning import (
            evaluate_retraining_need,
        )

        status = evaluate_retraining_need()
        ACTIVE_LEARNING_TOTAL.set(status.unconsumed_total)
        ACTIVE_LEARNING_UNDERCLASS.set(status.pending_underclass)
    except Exception:  # noqa: BLE001
        # DB 미가용·import 실패 시 게이지 값 유지
        pass


@router.get("/metrics-prom", include_in_schema=False)
def metrics_prom() -> Response:
    """Prometheus exposition. /api/v1 접두 아래 등록되므로 실제 경로는
    /api/v1/metrics-prom (OpenAPI /metrics/* 와 충돌하지 않게 -prom 접미).
    """
    _refresh_business_gauges()
    data = generate_latest(registry)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
