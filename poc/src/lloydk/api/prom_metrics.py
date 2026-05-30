"""Prometheus instrumentation — FastAPI 미들웨어 + /metrics-prom 엔드포인트.

설계:
- PrometheusMiddleware: 요청수·지연·in-progress·에러를 자동 수집
- /api/v1/metrics-prom: prometheus 표준 exposition (text/plain)
- /healthz·/metrics-prom 자체는 스크랩 제외 (셀프 카운트 회피)
- route 라벨은 path template (예: /api/v1/classify/{doc_id}) — 카디널리티 폭증 방지
- active_learning 게이지는 endpoint lazy 갱신 + 백그라운드 주기 refresh 병행
  (METRICS_REFRESH_INTERVAL_SEC, 기본 60초). 테스트 환경(TESTING=1)에서는 자동 비활성.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Awaitable, Callable

from fastapi import APIRouter, FastAPI, Request, Response
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    CollectorRegistry,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
)
from starlette.middleware.base import BaseHTTPMiddleware

_logger = logging.getLogger(__name__)

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

# L2: 임베딩 모델 로드 실패 → fallback 사용 (정확도 저하 위험) 카운트
EMBEDDING_FALLBACK_TOTAL = Counter(
    "lloydk_embedding_fallback_total",
    "Embedding provider fallback to HashEmbedding (model load failed)",
    ["original_provider"],
    registry=registry,
)

# §7 (2026-05-29): /answer 단계별 latency — retrieve(쿼리 확장 + ES 검색 + reranker)
# vs synthesize(LLM 답안 합성). 운영 SLO 정의 + §1 batch encode 효과 정량 입증.
ANSWER_PHASE_DURATION = Histogram(
    "lloydk_answer_phase_duration_seconds",
    "POST /answer per-phase latency",
    ["phase"],  # retrieve | synthesize
    buckets=(0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
    registry=registry,
)

# §4 (2026-05-29): CachedEmbedding 적중률 측정 — Redis 캐시 ROI 추적.
# 운영 진입 시 캐시 적중률 = 임베딩 API 비용 절감 근거.
EMBEDDING_CACHE_HIT_TOTAL = Counter(
    "lloydk_embedding_cache_hit_total",
    "Embedding cache hits (LRU or redis)",
    ["layer"],  # lru | redis | disk
    registry=registry,
)
EMBEDDING_CACHE_MISS_TOTAL = Counter(
    "lloydk_embedding_cache_miss_total",
    "Embedding cache misses (forwarded to underlying embedder)",
    registry=registry,
)

# A4 (2026-05-29): Drift monitor — P1-B4를 운영 신호로 살리기.
# Celery beat가 drift_tick 호출 → compute_drift() → 본 gauge에 set().
DRIFT_KL_DIVERGENCE = Gauge(
    "lloydk_drift_kl_divergence",
    "KL divergence between train and recent prod embedding cosine distributions",
    registry=registry,
)
DRIFT_COSINE_MEAN = Gauge(
    "lloydk_drift_cosine_mean",
    "Mean cosine similarity of recent prod embeddings vs train centroid",
    registry=registry,
)
DRIFT_COSINE_STD = Gauge(
    "lloydk_drift_cosine_std",
    "Std of cosine similarity of recent prod embeddings",
    registry=registry,
)
DRIFT_ALERT = Gauge(
    "lloydk_drift_alert",
    "1 if KL divergence >= threshold (drift suspected), else 0",
    registry=registry,
)
DRIFT_SAMPLE_SIZE = Gauge(
    "lloydk_drift_sample_size",
    "Number of prod embedding samples included in the latest drift report",
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
    """카디널리티 안정 — path template 우선, 라우팅 실패 시 'unknown'으로 collapse.

    O1: raw path 노출하면 /api/v1/classify/{uuid}처럼 ID 포함된 경로가
    수많은 라벨 값을 만들어 Prometheus storage 폭증 위험. 매칭 실패는 unknown으로.
    """
    route = request.scope.get("route")
    if route is not None and getattr(route, "path", None):
        return route.path
    # 라우팅 실패 시 — 카디널리티 폭증 차단
    return "unknown"


class PrometheusMiddleware(BaseHTTPMiddleware):
    """요청별 메트릭 자동 수집. AuditMiddleware보다 안쪽에 두면 안 됨 (라우팅 끝나야 route_template 확보)."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path
        if path in _EXCLUDED:
            return await call_next(request)

        method = request.method
        start = time.perf_counter()
        # O2: route_label은 측정 진입 시점에 unknown으로 초기화. 라우팅 후 갱신.
        # INPROGRESS는 진입 즉시 inc해야 finally에서 짝맞는 dec 가능 (언더플로우 차단).
        route_label = "unknown"
        INPROGRESS.labels(method=method, route=route_label).inc()
        inprogress_inc = True

        try:
            response = await call_next(request)
            route_label = _route_template(request)
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
            # 짝맞춰 dec — O2: 위에서 unknown 라벨로 inc했으므로 동일 라벨로 dec 가능
            if inprogress_inc:
                try:
                    INPROGRESS.labels(method=method, route="unknown").dec()
                except Exception as exc:  # noqa: BLE001
                    # K3: 빈 swallow 제거, warning 기록
                    _logger.warning("INPROGRESS dec failed: %s", exc)


# ============================================================
# /metrics-prom router
# ============================================================

router = APIRouter()


def _refresh_business_gauges() -> None:
    """active_learning gauge를 호출 시점에 lazy 갱신.

    실패해도 메트릭 노출 자체는 중단되지 않게 best-effort.
    K3: 실패 시 debug 로깅 — DB 미가용은 정상 상태(테스트)일 수 있어 warning 대신 debug.
    """
    try:
        from lloydk.modules.m6_evaluation.active_learning import (
            evaluate_retraining_need,
        )

        status = evaluate_retraining_need()
        ACTIVE_LEARNING_TOTAL.set(status.unconsumed_total)
        ACTIVE_LEARNING_UNDERCLASS.set(status.pending_underclass)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("business gauge refresh skipped: %s", exc)


@router.get("/metrics-prom", include_in_schema=False)
def metrics_prom() -> Response:
    """Prometheus exposition. /api/v1 접두 아래 등록되므로 실제 경로는
    /api/v1/metrics-prom (OpenAPI /metrics/* 와 충돌하지 않게 -prom 접미).
    """
    _refresh_business_gauges()
    data = generate_latest(registry)
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)


# ============================================================
# Background refresh — active_learning gauge 주기 갱신
# ============================================================
#
# /metrics-prom 호출이 없으면 게이지가 stale 상태로 남는 문제 해결.
# Grafana/Prometheus 폴링이 30~60초인 환경에서도 항상 최신 값 노출.


def _resolve_refresh_interval() -> float:
    raw = os.getenv("METRICS_REFRESH_INTERVAL_SEC", "60").strip()
    try:
        v = float(raw)
        return v if v > 0 else 0.0
    except ValueError:
        return 60.0


def _is_testing() -> bool:
    """pytest / TestClient 환경 자동 감지 — background task 비활성."""
    if os.getenv("TESTING", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    if os.getenv("PYTEST_CURRENT_TEST"):
        return True
    return False


async def _gauge_refresh_loop(interval: float) -> None:
    """무한 루프 — interval 마다 _refresh_business_gauges 호출.

    cancel 받으면 즉시 종료. 예외는 삼키고 다음 사이클로 진행.
    """
    while True:
        try:
            _refresh_business_gauges()
        except Exception as exc:  # noqa: BLE001
            _logger.warning("gauge refresh failed: %s", exc)
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            break


def register_background_refresh(app: FastAPI) -> None:
    """FastAPI startup/shutdown 훅 등록 — 메인 lifespan 외부에서 호출.

    테스트 환경(TESTING=1 또는 pytest 실행 중)에서는 task 자체를 만들지 않는다.
    interval <= 0 이면 사용자가 명시적으로 disable 한 것으로 간주.
    """
    if _is_testing():
        return

    interval = _resolve_refresh_interval()
    if interval <= 0:
        return

    async def _start_refresh_task() -> None:  # noqa: D401
        loop_task = asyncio.create_task(_gauge_refresh_loop(interval))
        app.state._prom_refresh_task = loop_task

    async def _stop_refresh_task() -> None:  # noqa: D401
        task: asyncio.Task | None = getattr(app.state, "_prom_refresh_task", None)
        if task is None:
            return
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    app.router.on_startup.append(_start_refresh_task)
    app.router.on_shutdown.append(_stop_refresh_task)
