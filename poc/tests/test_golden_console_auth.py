from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from koipa.api import _jwt_auth
from koipa.api.console_login import _console_actor_id
from koipa.api.golden import _render_specledger_gold_console_html
from koipa.api._jwt_auth import JWTClaims
from koipa.config import settings


def _request() -> Request:
    return Request({"type": "http", "method": "GET", "path": "/", "headers": []})


def test_console_requires_jwt_subject_not_shared_api_key():
    claims = JWTClaims(sub="portal-admin-kim", roles=("admin",), exp=9999999999)
    assert _console_actor_id({"mode": "jwt", "claims": claims}) == "portal-admin-kim"
    with pytest.raises(HTTPException, match="portal JWT"):
        _console_actor_id({"mode": "api_key", "actor_role": "admin"})


def test_jwt_cookie_is_accepted_without_authorization_header(monkeypatch):
    claims = JWTClaims(sub="portal-admin-kim", roles=("admin",), exp=9999999999)
    monkeypatch.setattr(settings, "auth_mode", "jwt")
    monkeypatch.setattr(_jwt_auth, "verify_jwt", lambda token: claims)
    auth = _jwt_auth.require_auth(
        _request(), authorization=None, x_api_key=None, koipa_access_token="http-only-cookie-token"
    )
    assert auth["mode"] == "jwt" and auth["claims"].sub == "portal-admin-kim"


def test_both_mode_prefers_console_jwt_over_a_stale_stored_api_key(monkeypatch):
    """로그인한 콘솔은 오래 남은 API 키보다 JWT 개인 신원으로 감사돼야 한다."""
    claims = JWTClaims(sub="portal-admin-kim", roles=("admin",), exp=9999999999)
    request = _request()
    monkeypatch.setattr(settings, "auth_mode", "both")
    monkeypatch.setattr(settings, "api_key", "legacy-browser-key")
    monkeypatch.setattr(_jwt_auth, "verify_jwt", lambda token: claims)
    auth = _jwt_auth.require_auth(
        request,
        authorization=None,
        x_api_key="legacy-browser-key",
        koipa_access_token="console-jwt-cookie",
    )
    assert auth["mode"] == "jwt"
    assert request.state.auth_actor == "portal-admin-kim"


def test_none_mode_still_honors_a_console_login_cookie(monkeypatch):
    """[2026-10-10] KL 요청 — "AI API 인증 생략"과 "검수 콘솔 로그인"은 별개다.

    auth_mode=none 이어도 로그인 쿠키가 있으면 그 개인 신원(jwt)을 쓴다 — 그래야
    console_login._console_actor_id 가 공유 role 로 뭉개지 않고 실제 검수자를 식별해
    등급확정·폐기 이력에 책임자를 남길 수 있다.
    """
    claims = JWTClaims(sub="reviewer-07", roles=("reviewer",), exp=9999999999)
    monkeypatch.setattr(settings, "auth_mode", "none")
    monkeypatch.setattr(_jwt_auth, "verify_jwt", lambda token: claims)
    auth = _jwt_auth.require_auth(
        _request(), authorization=None, x_api_key=None, koipa_access_token="reviewer-cookie-token"
    )
    assert auth["mode"] == "jwt" and auth["claims"].sub == "reviewer-07"
    assert _console_actor_id(auth) == "reviewer-07"


def test_none_mode_without_cookie_falls_back_to_configured_role(monkeypatch):
    """쿠키가 없으면(서버간 호출 등) 기존 none 동작 그대로 — 회귀 방지."""
    monkeypatch.setattr(settings, "auth_mode", "none")
    monkeypatch.setattr(settings, "api_key_role", "kl_backend")
    auth = _jwt_auth.require_auth(
        _request(), authorization=None, x_api_key=None, koipa_access_token=None
    )
    assert auth["mode"] == "none" and auth["actor_role"] == "kl_backend"


def test_none_mode_with_broken_cookie_falls_back_instead_of_raising(monkeypatch):
    """깨진/만료된 콘솔 쿠키가 있어도 none 모드 자체가 죽지는 않는다 — 조용히 폴백."""
    def _raise(token):
        raise _jwt_auth.JWTError("expired")

    monkeypatch.setattr(settings, "auth_mode", "none")
    monkeypatch.setattr(settings, "api_key_role", "system")
    monkeypatch.setattr(_jwt_auth, "verify_jwt", _raise)
    auth = _jwt_auth.require_auth(
        _request(), authorization=None, x_api_key=None, koipa_access_token="stale-cookie"
    )
    assert auth["mode"] == "none" and auth["actor_role"] == "system"


def test_console_has_no_manual_key_or_actor_inputs():
    html = _render_specledger_gold_console_html()
    assert 'id="apiKey"' not in html
    assert 'id="actor"' not in html
    assert "X-API-Key" not in html
    assert "credentials:'same-origin'" in html
    assert "api+'/session'" in html
    assert "window.__GOLDEN_PREVIEW__" in html
    # 브랜드는 발주기관 기준이다. 검수자가 여는 화면이라 공급사명이 앞에 서면 안 된다.
    assert "한국지식재산보호원" in html
    # [2026-08-24] 「검증문서 검수 목록」 확인을 뺐다 — 그 메뉴 항목 자체가 없어졌다
    # (console_nav.py, 4항목 → 3항목: 그 항목만 다른 화면이 아니라 관리자 콘솔의 내부
    # 앵커였다). 이 화면에 공용 메뉴가 붙어 있다는 사실은 test_console_header_unified.py
    # 가 LABELS 로 잠그므로, 여기서 특정 항목 이름을 또 박아 둘 필요가 없다.
    assert "검증문서 후보 관리" in html


def test_real_document_intake_requires_provenance_fields_in_ui():
    """실문서 등록에 출처·권한 근거 칸이 있는지.

    [D1 2026-08-17] 대상이 별도 화면(actual-intake.html)에서 **후보 관리의 업로드 모달**로
    바뀌었다. 두 화면이 같은 API(/golden/candidates/upload)·같은 필드를 쓰는데 화면만
    둘이었고, 등록 기준 안내는 한쪽에만 있었다(업로드하는 자리에서 기준을 못 봤다).
    검사 대상만 옮기고 **무엇을 잠그는지는 그대로다.**
    """
    html = _render_specledger_gold_console_html()
    assert 'id="upOrigin"' in html
    assert 'id="upSource"' in html and 'id="upBasis"' in html
    assert "organization_real" in html and "public_real" in html
    # [2026-08-23] 화면 표기를 「평가 정답지」로 옮겼다(Locked Gold 는 개발 용어).
    # 잠그는 것은 그대로다 — **여기 등록이 승격이 아니라는 사실**이 화면에 있어야 한다.
    assert "여기 등록은 평가 정답지 승격이 아닙니다" in html
    # 흡수한 등록 기준 4블록이 실제로 왔는지
    for block in ("S3 · 공개·일반", "S2 · 조직 내부", "제외", "다음 단계"):
        assert block in html, f"등록 기준 '{block}' 이 모달에 없다"


def test_actual_intake_screen_is_gone():
    """별도 화면·라우트가 남아 있으면 두 자리에서 같은 일을 하게 된다."""
    import koipa.api.golden as g

    assert not hasattr(g, "_render_actual_document_intake_html")
    assert not hasattr(g, "proxy_gold_actual_document_intake_html")

    from koipa.api.app import app

    paths = set(app.openapi().get("paths") or {})
    assert not any("actual-intake" in p for p in paths), "라우트가 남아 있다"
