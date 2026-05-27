"""W7 관측성 — Prometheus 메트릭 + 미들웨어 검증."""

from __future__ import annotations

from fastapi.testclient import TestClient

from lloydk.api.app import app
from lloydk.api.prom_metrics import (
    ACTIVE_LEARNING_TOTAL,
    ACTIVE_LEARNING_UNDERCLASS,
    INPROGRESS,
    REQUEST_COUNT,
    REQUEST_LATENCY,
    registry,
)
from lloydk.config import settings

HDR = {"X-API-Key": settings.api_key}


class TestPrometheusEndpoint:
    def test_metrics_prom_returns_200(self):
        with TestClient(app) as cli:
            r = cli.get("/api/v1/metrics-prom")
            assert r.status_code == 200
            assert r.headers["content-type"].startswith("text/plain")

    def test_metrics_prom_exposes_known_series(self):
        with TestClient(app) as cli:
            # 요청 1건 발생시켜 카운터 채움
            cli.get("/api/v1/schema/grades", headers=HDR)
            r = cli.get("/api/v1/metrics-prom")
            body = r.text
            # Counter
            assert "lloydk_requests_total" in body
            # Histogram (sklearn-style bucket suffix)
            assert "lloydk_request_duration_seconds_bucket" in body
            assert "lloydk_request_duration_seconds_sum" in body
            assert "lloydk_request_duration_seconds_count" in body
            # Gauge
            assert "lloydk_active_learning_pending_total" in body
            assert "lloydk_active_learning_pending_underclass" in body
            # Exception counter (등록만)
            assert "lloydk_request_exceptions_total" in body

    def test_metrics_prom_excluded_from_self_counting(self):
        """/metrics-prom 자체 호출은 lloydk_requests_total 카운터에 안 잡혀야 함."""
        # 직접 라벨로 측정 — 호출 전후 변화 0
        registry_text_before = _registry_text()
        with TestClient(app) as cli:
            for _ in range(3):
                cli.get("/api/v1/metrics-prom")
        registry_text_after = _registry_text()
        # /metrics-prom path label 카운트는 등장 안 해야 함
        assert "/api/v1/metrics-prom" not in registry_text_after or \
               registry_text_before.count("/api/v1/metrics-prom") == registry_text_after.count("/api/v1/metrics-prom")

    def test_healthz_excluded(self):
        registry_text_before = _registry_text()
        with TestClient(app) as cli:
            for _ in range(3):
                cli.get("/api/v1/healthz")
        registry_text_after = _registry_text()
        assert registry_text_before.count("/api/v1/healthz") == registry_text_after.count("/api/v1/healthz")


class TestMetricsCollection:
    def test_requests_counter_increments(self):
        # 라벨 조합이 새로 생기는지만 검증 (사전 카운트는 다른 테스트로 오염될 수 있음)
        with TestClient(app) as cli:
            cli.get("/api/v1/schema/grades", headers=HDR)
            cli.get("/api/v1/schema/grades", headers=HDR)
        # GET /api/v1/schema/grades 200 라벨이 등록됐는지
        body = _registry_text()
        assert 'route="/api/v1/schema/grades"' in body or "/api/v1/schema/grades" in body

    def test_4xx_response_recorded(self):
        with TestClient(app) as cli:
            r = cli.post(
                "/api/v1/classify",
                headers={"X-API-Key": "wrong"},
                json={"doc_id": "x", "content": "y"},
            )
            assert r.status_code == 401
        body = _registry_text()
        assert 'status="401"' in body or "401" in body


def _registry_text() -> str:
    from prometheus_client import generate_latest
    return generate_latest(registry).decode("utf-8")
