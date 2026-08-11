"""메트릭 route 라벨이 전체 경로를 담는지 고정 — DB 없이 항상 도는 회귀.

배경. 이 FastAPI/Starlette 버전은 scope['route'] 에 **라우터 상대** 경로를 담는다.
include_router(prefix='/api/v1') 로 등록해도 route.path 는 '/classify/{doc_id}' 라,
그대로 라벨로 쓰면 route="/schema/grades" 처럼 /api/v1 이 사라진다.
DEF-2026-38(OpenAPI 경로 수집이 같은 이유로 틀렸던 것)과 같은 뿌리다.

기존 test_observability.py 가 이 계약을 이미 적어 두고 있었지만 `skipif(not _PG)` 라
Postgres 없는 로컬에서는 건너뛰어, 실제로는 아무도 안 보고 있었다. 여기는 DB 를 요구하지
않으므로 항상 돈다.

함께 지키는 것: 파라미터 경로는 **템플릿**으로 남아야 한다. 구체 ID 가 라벨이 되면
Prometheus 라벨 카디널리티가 폭증한다(원래 _route_template 의 존재 이유).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from lloydk.api.prom_metrics import _mount_prefix, _route_template


class _Req:
    """scope 만 있으면 되는 최소 대역 — Request 전체를 만들 필요가 없다."""

    def __init__(self, path: str, params: dict | None = None, route: object = None):
        self.scope = {"path": path, "path_params": params or {}}
        if route is not None:
            self.scope["route"] = route


class _Route:
    def __init__(self, path: str):
        self.path = path


# ── 접두사 복원 ────────────────────────────────────────────────────────────────

def test_prefix_restored_for_static_path():
    req = _Req("/api/v1/schema/grades")
    assert _mount_prefix(req, "/schema/grades") == "/api/v1/schema/grades"


def test_prefix_restored_while_template_is_preserved():
    """구체 ID 가 아니라 {doc_id} 가 남아야 카디널리티가 안 터진다."""
    req = _Req(
        "/api/v1/classify/6f1c9d2e-0000-0000-0000-000000000000",
        {"doc_id": "6f1c9d2e-0000-0000-0000-000000000000"},
    )
    assert _mount_prefix(req, "/classify/{doc_id}") == "/api/v1/classify/{doc_id}"


def test_multi_param_template():
    req = _Req("/api/v1/review-queue/abc/evidence", {"classification_id": "abc"})
    got = _mount_prefix(req, "/review-queue/{classification_id}/evidence")
    assert got == "/api/v1/review-queue/{classification_id}/evidence"


def test_no_prefix_leaves_template_unchanged():
    req = _Req("/healthz")
    assert _mount_prefix(req, "/healthz") == "/healthz"


def test_unrecoverable_prefix_falls_back_to_template():
    """복원 실패는 종전 값 유지 — 라벨은 부가 정보이고 요청을 막으면 안 된다."""
    req = _Req("/totally/other", {"x": "1"})
    assert _mount_prefix(req, "/thing/{x}") == "/thing/{x}"


# ── _route_template 계약 ──────────────────────────────────────────────────────

def test_route_template_collapses_to_unknown_without_route():
    assert _route_template(_Req("/api/v1/anything")) == "unknown"


def test_route_template_applies_prefix():
    req = _Req("/api/v1/schema/grades", route=_Route("/schema/grades"))
    assert _route_template(req) == "/api/v1/schema/grades"


# ── 실제 앱 경유 ──────────────────────────────────────────────────────────────

@pytest.fixture
def labels():
    from prometheus_client import generate_latest

    from lloydk.api.prom_metrics import PrometheusMiddleware, registry

    app = FastAPI()
    app.add_middleware(PrometheusMiddleware)
    sub = FastAPI()

    @sub.get("/grades")
    def grades():
        return {"ok": True}

    @sub.get("/item/{item_id}")
    def item(item_id: str):
        return {"id": item_id}

    from fastapi import APIRouter

    router = APIRouter()
    router.add_api_route("/grades", grades, methods=["GET"])
    router.add_api_route("/item/{item_id}", item, methods=["GET"])
    app.include_router(router, prefix="/api/v1/schema")

    with TestClient(app) as client:
        client.get("/api/v1/schema/grades")
        client.get("/api/v1/schema/item/xyz-123")
    return generate_latest(registry).decode("utf-8")


def test_live_request_records_full_path(labels):
    assert 'route="/api/v1/schema/grades"' in labels


def test_live_parameterized_request_records_template_not_id(labels):
    assert 'route="/api/v1/schema/item/{item_id}"' in labels
    assert "xyz-123" not in labels, "구체 ID 가 라벨에 들어가면 카디널리티가 폭증한다"
