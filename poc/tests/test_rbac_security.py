"""RBAC 보안 회귀 테스트 — X-Actor-Role 헤더 위조 차단 + JWT roles 연결.

배경(2026-06-02): require_role이 검사하던 actor_role이
- api_key 모드에서 X-Actor-Role 헤더로 자칭 가능 (위조)
- jwt 모드에서 claims.roles와 미연결 → 항상 'system'으로 통과
였음. 본 테스트는 두 경로가 모두 닫혔는지 고정한다.
"""

from __future__ import annotations

import asyncio

import pytest

import types

from koipa.api import _jwt_auth
from koipa.api._jwt_auth import (
    JWTClaims,
    _highest_role,
    _resolve_api_key_roles,
    require_auth,
)
from koipa.api._rbac import require_role


class _FakeRequest:
    def __init__(self, headers: dict[str, str]):
        # 헤더 키는 소문자로 조회됨 (Starlette Headers 동작 모사)
        self.headers = {k.lower(): v for k, v in headers.items()}
        self.state = types.SimpleNamespace()


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


# ---------------------------------------------------------------------------
# [2026-10-06] api_key 교체 유예(rotation grace) — api_key_previous
# ---------------------------------------------------------------------------

def test_require_auth_accepts_current_key(monkeypatch):
    monkeypatch.setattr(_jwt_auth.settings, "auth_mode", "api_key")
    monkeypatch.setattr(_jwt_auth.settings, "api_key", "new-key")
    monkeypatch.setattr(_jwt_auth.settings, "api_key_previous", "old-key")
    req = _FakeRequest({})
    out = require_auth(req, authorization=None, x_api_key="new-key", koipa_access_token=None)
    assert out["mode"] == "api_key"


def test_require_auth_accepts_previous_key_during_rotation(monkeypatch):
    monkeypatch.setattr(_jwt_auth.settings, "auth_mode", "api_key")
    monkeypatch.setattr(_jwt_auth.settings, "api_key", "new-key")
    monkeypatch.setattr(_jwt_auth.settings, "api_key_previous", "old-key")
    req = _FakeRequest({})
    out = require_auth(req, authorization=None, x_api_key="old-key", koipa_access_token=None)
    assert out["mode"] == "api_key"


def test_require_auth_rejects_unrelated_key_even_with_rotation_configured(monkeypatch):
    monkeypatch.setattr(_jwt_auth.settings, "auth_mode", "api_key")
    monkeypatch.setattr(_jwt_auth.settings, "api_key", "new-key")
    monkeypatch.setattr(_jwt_auth.settings, "api_key_previous", "old-key")
    req = _FakeRequest({})
    with pytest.raises(_jwt_auth.HTTPException) as exc:
        require_auth(req, authorization=None, x_api_key="someone-else", koipa_access_token=None)
    assert exc.value.status_code == 401


def test_require_auth_previous_key_empty_by_default_does_not_widen_acceptance(monkeypatch):
    """api_key_previous 를 안 쓰면(빈 문자열) 옛 키 비교 자체를 안 한다 — 평소 동작 불변."""
    monkeypatch.setattr(_jwt_auth.settings, "auth_mode", "api_key")
    monkeypatch.setattr(_jwt_auth.settings, "api_key", "new-key")
    monkeypatch.setattr(_jwt_auth.settings, "api_key_previous", "")
    req = _FakeRequest({})
    with pytest.raises(_jwt_auth.HTTPException):
        require_auth(req, authorization=None, x_api_key="", koipa_access_token=None)


# ---------------------------------------------------------------------------
# [2026-10-07] auth_mode=none — 사용자 결정(폐쇄망 전용 배포), 검증 전부 생략
# ---------------------------------------------------------------------------

def test_require_auth_none_mode_needs_no_key_at_all(monkeypatch):
    monkeypatch.setattr(_jwt_auth.settings, "auth_mode", "none")
    monkeypatch.setattr(_jwt_auth.settings, "api_key", "")
    monkeypatch.setattr(_jwt_auth.settings, "api_key_role", "system")
    req = _FakeRequest({})
    out = require_auth(req, authorization=None, x_api_key=None, koipa_access_token=None)
    assert out["mode"] == "none"
    assert out["actor_role"] == "system"


def test_require_auth_none_mode_uses_configured_role_not_blanket_admin(monkeypatch):
    """인증 생략이 전권(admin) 승인으로 저절로 번지지 않는다 — api_key_role 그대로 쓴다."""
    monkeypatch.setattr(_jwt_auth.settings, "auth_mode", "none")
    monkeypatch.setattr(_jwt_auth.settings, "api_key_role", "kl_backend")
    req = _FakeRequest({})
    out = require_auth(req, authorization=None, x_api_key=None, koipa_access_token=None)
    assert out["actor_role"] == "kl_backend"
    assert "admin" not in out["actor_roles"]


def test_require_auth_none_mode_rejected_by_admin_only_route():
    """none 모드도 역할은 그대로 검사된다 — api_key_role 이 admin 이 아니면 admin 전용 라우트는 여전히 403."""
    ctx = {"mode": "none", "actor_role": "kl_backend", "actor_roles": ("kl_backend",)}
    with pytest.raises(_jwt_auth.HTTPException) as exc:
        _run_require_role(("admin",), ctx)
    assert exc.value.status_code == 403
