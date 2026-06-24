"""RBAC 보안 회귀 테스트 — X-Actor-Role 헤더 위조 차단 + JWT roles 연결.

배경(2026-06-02): require_role이 검사하던 actor_role이
- api_key 모드에서 X-Actor-Role 헤더로 자칭 가능 (위조)
- jwt 모드에서 claims.roles와 미연결 → 항상 'system'으로 통과
였음. 본 테스트는 두 경로가 모두 닫혔는지 고정한다.
"""

from __future__ import annotations

import asyncio

import pytest

from lloydk.api import _jwt_auth
from lloydk.api._jwt_auth import (
    JWTClaims,
    _highest_role,
    _resolve_api_key_roles,
)
from lloydk.api._rbac import require_role


class _FakeRequest:
    def __init__(self, headers: dict[str, str]):
        # 헤더 키는 소문자로 조회됨 (Starlette Headers 동작 모사)
        self.headers = {k.lower(): v for k, v in headers.items()}


def _run_require_role(allowed: tuple[str, ...], auth_context: dict) -> dict:
    """require_role(allowed)의 의존성 함수를 직접 실행."""
    checker = require_role(*allowed)
    return asyncio.run(checker(auth_context))


# ---------------------------------------------------------------------------
# _highest_role
# ---------------------------------------------------------------------------

def test_highest_role_picks_max_privilege():
    assert _highest_role(("system", "admin")) == "admin"
    assert _highest_role(("reviewer", "kl_backend")) == "kl_backend"
    assert _highest_role(("system",)) == "system"


def test_highest_role_ignores_unknown_and_empty():
    assert _highest_role(("garbage", "x")) == ""
    assert _highest_role(()) == ""


# ---------------------------------------------------------------------------
# api_key 역할 결정 — 헤더 위조 차단
# ---------------------------------------------------------------------------

def test_api_key_role_ignores_header_when_untrusted(monkeypatch):
    """trust 플래그 off(운영 기본): X-Actor-Role 헤더는 무시되고 서버 설정 역할만."""
    monkeypatch.setattr(_jwt_auth.settings, "api_key_trust_actor_role_header", False)
    monkeypatch.setattr(_jwt_auth.settings, "api_key_role", "system")
    req = _FakeRequest({"X-Actor-Role": "admin"})  # admin 자칭 시도
    assert _resolve_api_key_roles(req) == ("system",)  # 무시됨


def test_api_key_role_uses_header_when_trusted(monkeypatch):
    """trust 플래그 on(개발·테스트): 검증된 enum 헤더값 채택."""
    monkeypatch.setattr(_jwt_auth.settings, "api_key_trust_actor_role_header", True)
    req = _FakeRequest({"X-Actor-Role": "admin"})
    assert _resolve_api_key_roles(req) == ("admin",)


def test_api_key_role_rejects_invalid_header_role(monkeypatch):
    """trust 플래그 on이라도 enum 외 값은 403."""
    from fastapi import HTTPException
    monkeypatch.setattr(_jwt_auth.settings, "api_key_trust_actor_role_header", True)
    req = _FakeRequest({"X-Actor-Role": "superadmin"})
    with pytest.raises(HTTPException) as ei:
        _resolve_api_key_roles(req)
    assert ei.value.status_code == 403


def test_api_key_role_rejects_invalid_configured_role(monkeypatch):
    from fastapi import HTTPException
    monkeypatch.setattr(_jwt_auth.settings, "api_key_trust_actor_role_header", False)
    monkeypatch.setattr(_jwt_auth.settings, "api_key_role", "superadmin")
    with pytest.raises(HTTPException) as ei:
        _resolve_api_key_roles(_FakeRequest({}))
    assert ei.value.status_code == 403


# ---------------------------------------------------------------------------
# require_role — JWT roles 연결 + 권한 부족 차단
# ---------------------------------------------------------------------------

def test_require_role_allows_matching_jwt_role():
    ctx = {"mode": "jwt", "actor_role": "admin", "actor_roles": ("admin",)}
    assert _run_require_role(("admin", "kl_backend"), ctx) is ctx


def test_require_role_blocks_empty_jwt_roles():
    """roles claim이 비면(VALID_ROLES 교집합 없음) 권한 부족 403 — 과거엔 system으로 통과."""
    from fastapi import HTTPException
    ctx = {"mode": "jwt", "actor_role": "", "actor_roles": ()}
    with pytest.raises(HTTPException) as ei:
        _run_require_role(("admin", "kl_backend", "system"), ctx)
    assert ei.value.status_code == 403


def test_require_role_blocks_insufficient_role():
    from fastapi import HTTPException
    ctx = {"mode": "api_key", "actor_role": "system", "actor_roles": ("system",)}
    with pytest.raises(HTTPException) as ei:
        _run_require_role(("admin",), ctx)  # admin 전용 라우터
    assert ei.value.status_code == 403
    assert ei.value.status_code == 403


def test_require_role_multi_role_intersection():
    """JWT가 복수 역할 보유 시 교집합 하나라도 있으면 통과."""
    ctx = {"mode": "jwt", "actor_role": "admin", "actor_roles": ("reviewer", "admin")}
    assert _run_require_role(("admin",), ctx) is ctx


def test_jwt_claims_roles_wiring():
    """verify_jwt 결과 형태 모사: VALID_ROLES만 채택돼 actor_role에 반영되는지."""
    claims = JWTClaims(sub="u1", roles=("admin", "garbage"))
    valid = tuple(r for r in claims.roles if r in _jwt_auth.VALID_ROLES)
    assert valid == ("admin",)
    assert _highest_role(valid) == "admin"
