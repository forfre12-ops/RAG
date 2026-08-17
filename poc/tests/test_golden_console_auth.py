from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from koipa.api import _jwt_auth
from koipa.api.golden import (
    _console_actor_id,
    _render_specledger_gold_console_html,
)
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
    assert "골든셋 검수" in html


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
    assert "Locked Gold" in html
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
