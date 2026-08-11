from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from lloydk.api import _jwt_auth
from lloydk.api.golden import (
    _console_actor_id,
    _render_actual_document_intake_html,
    _render_specledger_gold_console_html,
)
from lloydk.api._jwt_auth import JWTClaims
from lloydk.config import settings


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
        _request(), authorization=None, x_api_key=None, lloydk_access_token="http-only-cookie-token"
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
    html = _render_actual_document_intake_html()
    assert 'id="origin"' in html
    assert 'id="source"' in html and 'id="basis"' in html
    assert "organization_real" in html and "public_real" in html
    assert "Locked Gold" in html
