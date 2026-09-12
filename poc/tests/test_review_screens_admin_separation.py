"""검수 화면과 관리자 권한을 가른다 — 설계단계 감리 지적(2026-08-31~09-04)의 조치(2026-09-11).

KL 이 감리 결과를 전하며 이렇게 적었다: "골든셋 검증 페이지에서 관리자 콘솔로 접근이 가능한 상태여서
설계단계 감리임에도 실제 분류기 테스트까지 진행하였습니다." 세 가지가 겹쳐 있었다.

  ① 검수 화면(로그인 · 후보 관리 · 검수/서명) 상단 메뉴에 「관리자 콘솔」 링크가 있었다.
  ② 후보 관리 화면과 등급 결정이 admin · kl_backend 역할만 받아, 검수자에게도 관리자 토큰을 줘야 했다
     (토큰 발급 스크립트 기본값도 --roles admin 이었다).
  ③ 그 토큰의 쿠키(path=/)가 관리자 콘솔 로그인까지 겸했다.

관리자 API 는 이미 역할을 검사한다(admin 전용). 그래서 검수자 역할을 실제로 쓰게 하고 길을 없애면 갈린다.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from koipa.api._jwt_auth import JWTClaims, require_auth
from koipa.api.app import app
from koipa.api.golden import _render_console_login_html, _render_specledger_gold_console_html
from koipa.golden_review_html import _nav_html

ADMIN_HREF = "/console/admin.html"
_SETUP = Path(__file__).resolve().parents[1] / "scripts" / "setup_console_test_login.py"


def _auth(*roles: str) -> dict:
    claims = JWTClaims(sub="kl-reviewer-kim", roles=roles, exp=9999999999)
    return {"mode": "jwt", "claims": claims, "actor_role": roles[0], "actor_roles": list(roles)}


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.pop(require_auth, None)


def test_review_screens_do_not_link_to_the_admin_console() -> None:
    for name, html in (
        ("manage", _render_specledger_gold_console_html()),
        ("login", _render_console_login_html()),
        ("review", _nav_html("검수", "full-train", "review")),
    ):
        assert ADMIN_HREF not in html, f"{name}: 검수 화면에 관리자 콘솔 링크가 있다"


def test_reviewer_opens_the_candidate_console_without_admin_controls(client) -> None:
    app.dependency_overrides[require_auth] = lambda: _auth("reviewer")
    r = client.get("/api/v1/golden/candidates/manage.html")
    assert r.status_code == 200, r.text[:200]
    assert "검수자 권한으로 열었습니다" in r.text
    assert "#openUpload,#promote,#provBox{display:none!important}" in r.text


def test_admin_view_is_unchanged(client) -> None:
    app.dependency_overrides[require_auth] = lambda: _auth("admin")
    r = client.get("/api/v1/golden/candidates/manage.html")
    assert r.status_code == 200
    assert "검수자 권한으로 열었습니다" not in r.text
    assert "#openUpload,#promote,#provBox{display:none!important}" not in r.text


def test_reviewer_can_record_a_decision_but_not_admin_only_actions(client) -> None:
    app.dependency_overrides[require_auth] = lambda: _auth("reviewer")
    # 결정 — 역할로는 막히지 않는다(없는 후보라 다른 이유로 실패할 수는 있다).
    r = client.post("/api/v1/golden/candidates/NO-SUCH-DOC/decision", json={})
    assert r.status_code != 403, r.text[:200]
    # 관리자 전용 — 업로드 · 출처 기록 · 평가정답 승격은 여전히 403.
    assert client.post("/api/v1/golden/candidates/promote", json={}).status_code == 403
    assert client.post("/api/v1/golden/candidates/NO-SUCH-DOC/provenance", json={}).status_code == 403
    assert client.post("/api/v1/golden/candidates/upload",
                       files={"file": ("a.txt", b"x", "text/plain")}).status_code == 403


def test_reviewer_still_cannot_use_admin_apis(client) -> None:
    app.dependency_overrides[require_auth] = lambda: _auth("reviewer")
    assert client.put("/api/v1/schema/grades", json={"grades": []}).status_code == 403


def test_issued_tokens_default_to_reviewer() -> None:
    src = _SETUP.read_text(encoding="utf-8")
    assert 'ap.add_argument("--roles", default="reviewer"' in src, "발급 기본 역할이 관리자로 돌아갔다"
