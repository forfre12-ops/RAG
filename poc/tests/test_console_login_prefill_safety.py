"""프리필 시연 로그인은 편의 기능이어도 토큰 노출ㆍ자동 세션 생성은 허용하지 않는다."""

from __future__ import annotations

from fastapi.testclient import TestClient

from koipa.api._jwt_auth import JWTClaims, require_auth
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


def test_legacy_manage_address_redirects_to_the_admin_console() -> None:
    """구형 검수 콘솔 북마크는 현재 관리자 콘솔로만 연다."""
    claims = JWTClaims(sub="kl-admin-test", roles=("admin",), exp=9999999999)
    app.dependency_overrides[require_auth] = lambda: {
        "mode": "jwt", "claims": claims, "actor_role": "admin", "actor_roles": ["admin"],
    }
    try:
        response = TestClient(app).get(
            "/api/v1/golden/candidates/manage.html", follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(require_auth, None)

    assert response.status_code == 307
    assert response.headers["location"] == "/console/admin.html"
    assert response.headers["cache-control"] == "no-store"
