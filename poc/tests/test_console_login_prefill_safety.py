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
    assert "prefill-login" in page
    assert '<textarea id="t"' not in page
    assert "토큰으로 로그인" not in page
    assert "document.cookie='koipa_access_token=" not in page


def test_login_page_uses_password_only_without_demo_prefill(monkeypatch):
    """운영자에게 JWT를 붙여넣게 하는 UI는 제공하지 않는다."""
    from koipa.api import console_login

    monkeypatch.setattr(settings, "console_login_prefill_token", "", raising=False)
    page = console_login._render_console_login_html()

    assert 'id="u"' in page and 'id="p"' in page and 'id="goPw"' in page
    assert 'id="goPrefill"' not in page
    assert '<textarea id="t"' not in page
    assert "토큰으로 로그인" not in page


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
    """관리자 북마크만 운영 콘솔로 보낸다."""
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


def test_reviewer_manage_address_returns_the_reviewer_workspace() -> None:
    """검수자는 같은 주소를 열어도 관리자 정적 화면으로 보내지 않는다."""
    claims = JWTClaims(sub="reviewer-test", roles=("reviewer",), exp=9999999999)
    app.dependency_overrides[require_auth] = lambda: {
        "mode": "jwt", "claims": claims, "actor_role": "reviewer", "actor_roles": ["reviewer"],
    }
    try:
        response = TestClient(app).get(
            "/api/v1/golden/candidates/manage.html", follow_redirects=False,
        )
    finally:
        app.dependency_overrides.pop(require_auth, None)

    assert response.status_code == 200
    assert "reviewer-workspace" in response.text
    assert 'href="/console/admin.html"' not in response.text


def test_reviewer_cannot_open_administrator_html_directly() -> None:
    """정적 파일 경로를 직접 열어도 검수자에게 관리자 메뉴를 주지 않는다."""
    claims = JWTClaims(sub="reviewer-test", roles=("reviewer",), exp=9999999999)
    app.dependency_overrides[require_auth] = lambda: {
        "mode": "jwt", "claims": claims, "actor_role": "reviewer", "actor_roles": ["reviewer"],
    }
    try:
        client = TestClient(app)
        console = client.get("/console/admin.html", follow_redirects=False)
        legacy_demo = client.get("/demo/admin.html", follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)

    assert console.status_code == 403
    assert legacy_demo.status_code == 403
    assert "AI 영업비밀 분류" not in console.text


def test_administrator_can_open_protected_administrator_html() -> None:
    claims = JWTClaims(sub="admin-test", roles=("admin",), exp=9999999999)
    app.dependency_overrides[require_auth] = lambda: {
        "mode": "jwt", "claims": claims, "actor_role": "admin", "actor_roles": ["admin"],
    }
    try:
        response = TestClient(app).get("/console/admin.html", follow_redirects=False)
    finally:
        app.dependency_overrides.pop(require_auth, None)

    assert response.status_code == 200
    assert "AI 영업비밀" in response.text
    assert response.headers["cache-control"] == "no-cache, must-revalidate"
