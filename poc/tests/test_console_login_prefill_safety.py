"""프리필 시연 로그인은 편의 기능이어도 토큰 노출ㆍ자동 세션 생성은 허용하지 않는다."""

from __future__ import annotations

from fastapi.testclient import TestClient

from koipa.api.app import app
from koipa.config import settings


def test_prefill_token_is_not_rendered_or_autologin_script(monkeypatch):
    from koipa.api import console_login

    token = "header.payload.signature"
    monkeypatch.setattr(settings, "console_login_prefill_token", token, raising=False)

    page = console_login._render_console_login_html()

    assert token not in page
    assert "id=\"goPrefill\"" in page
    assert "var v=$('t').value.trim();if(v)login(v,true)" not in page
    assert "prefill-login" in page


def test_prefill_endpoint_sets_httponly_cookie_only_when_configured(monkeypatch):
    monkeypatch.setattr(settings, "console_login_prefill_token", "header.payload.signature", raising=False)
    client = TestClient(app)

    response = client.post("/api/v1/golden/candidates/prefill-login")

    assert response.status_code == 200
    assert response.json() == {"ok": True, "mode": "demo-prefill"}
    assert "httponly" in response.headers["set-cookie"].lower()
    assert "header.payload.signature" not in response.text


def test_prefill_endpoint_is_absent_without_unsafe_configuration(monkeypatch):
    monkeypatch.setattr(settings, "console_login_prefill_token", "", raising=False)
    client = TestClient(app)

    response = client.post("/api/v1/golden/candidates/prefill-login")

    assert response.status_code == 404
