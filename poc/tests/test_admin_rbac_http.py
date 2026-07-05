"""admin 전용 라우트의 RBAC 강제를 HTTP 레벨에서 봉인 (lite 런 포함 — 무마커).

기존 HTTP RBAC 검증은 /admin/model/reload 1건뿐이고 @pytest.mark.fullstack 이라
lite 런(-m "not fullstack …")에서 제외됐다 → "admin 전용 게이트가 실제 요청을 막는다"가
fake-green 상태였다. 이 테스트는 admin-only 라우트에 무인증·비-admin·admin 요청을 보내
401/403 강제와 admin 통과를 실제 HTTP 스택으로 확인한다.

- read-only best-effort GET(dashboard·escalation-held·locked-readiness)은 admin 200까지 검증
  (DB 없어도 각 섹션 best-effort로 degraded 구조를 200으로 반환).
- 쓰기 라우트(model/activate)는 거부(403/401)만 확인 — 실제 배포 로직 검증은 fullstack.

lifespan(무거운 startup)을 열지 않는다(`with` 미사용): 인증 의존성은 요청 단위로 동작하므로
RBAC 판정에 startup 상태가 필요 없고, lite 런의 빠른 실행을 유지한다.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lloydk.api.app import app
from lloydk.config import settings

client = TestClient(app)

API = "/api/v1"
ADMIN_GET_ROUTES = [
    "/admin/dashboard",
    "/admin/escalation-held",
    "/admin/locked-readiness",
]


def _h(role: str | None = None, key: str | None = None) -> dict:
    h: dict[str, str] = {}
    if key is not None:
        h["X-API-Key"] = key
    if role is not None:
        h["X-Actor-Role"] = role
    return h


@pytest.mark.parametrize("path", ADMIN_GET_ROUTES)
def test_admin_get_requires_auth(path):
    # 인증 헤더 없음 → 401/403 (핸들러 도달 전 차단).
    r = client.get(API + path)
    assert r.status_code in (401, 403), (path, r.status_code, r.text[:200])


@pytest.mark.parametrize("path", ADMIN_GET_ROUTES)
def test_admin_get_rejects_non_admin(path):
    # 유효 API 키 + 비-admin 역할(reviewer) → 403.
    r = client.get(API + path, headers=_h(role="reviewer", key=settings.api_key))
    assert r.status_code == 403, (path, r.status_code, r.text[:200])


@pytest.mark.parametrize("path", ADMIN_GET_ROUTES)
def test_admin_get_allows_admin(path):
    # admin 역할 → 200 (best-effort, DB 없어도 degraded 구조 반환).
    r = client.get(API + path, headers=_h(role="admin", key=settings.api_key))
    assert r.status_code == 200, (path, r.status_code, r.text[:200])
    assert isinstance(r.json(), dict)


def test_admin_activate_rejects_non_admin():
    # 쓰기 라우트도 비-admin 거부 — 배포 로직 진입 전 차단(reviewer는 admin 아님).
    r = client.post(
        API + "/admin/model/activate",
        headers=_h(role="reviewer", key=settings.api_key),
        json={"version_label": "should-not-run", "force": False},
    )
    assert r.status_code == 403, (r.status_code, r.text[:200])


def test_admin_activate_rejects_missing_auth():
    r = client.post(
        API + "/admin/model/activate",
        json={"version_label": "should-not-run", "force": False},
    )
    assert r.status_code in (401, 403), (r.status_code, r.text[:200])
