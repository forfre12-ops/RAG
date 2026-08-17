"""[C2/C3-2 2026-08-17] 골든셋 서명자는 로그인 쿠키(JWT sub)로만 정해진다.

왜 이 시험이 필요한가 — 서명 화면이 **검수자 이름·API Key·역할을 사람이 타이핑**하게
돼 있었다. 그리고 서버는 auth_mode=both 에서 공유 API Key 가 먼저 통과하면
resolve_actor_user_id 가 클라이언트가 보낸 이름을 **그대로 돌려준다**(confirm.py:53).
설계 의도는 "신뢰된 KL 포털이 실 검수자를 전파한다" 였는데, 223 에서 호출자는 브라우저다.
두 사실이 만나면 원장에 남는 서명자는 자칭이 된다.

실측(223, 2026-08-17): locked_gold_eval 20건이 전원 hong.gildong · 19건이 같은 마이크로초
서명이었다. SIGNOFF_DEFAULT_REVIEWER 가 채워 준 이름을 그대로 제출한 결과다.

여기서 잠그는 것:
  1. 화면에 신원·키 입력칸이 없다 (자칭 경로 차단)
  2. 렌더러가 신원 프리필 인자를 다시 받지 못한다 (조용히 되돌아오는 것 차단)
  3. JWT sub 가 본문 자칭 이름을 이긴다
  4. 공유 API Key 는 자칭을 막지 못한다 — 그래서 1번이 필요하다는 근거
  5. reviewer 역할이 자기 신원을 읽을 수 있다 (없으면 화면이 403 으로 막힌다)
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from koipa.api._jwt_auth import VALID_ROLES, JWTClaims
from koipa.api.confirm import resolve_actor_user_id
from koipa.api.golden import proxy_gold_candidate_session
from koipa.golden_review_html import render_signoff_html
from koipa.schemas.common import Actor

_RECORDS = [
    {"doc_id": "d1", "grade": "S2", "text": "본 문서는 내부 관리 기준을 정한다.",
     "factors": {"S": 2, "V": 2, "M": 0}},
]


def _signoff_html() -> str:
    return render_signoff_html(_RECORDS, job_id="job-1", post_url="/api/v1/golden/jobs/job-1/signoff")


# ── 1. 화면에 자칭 입력칸이 없다 ──────────────────────────────────────────────

def test_signoff_page_has_no_identity_or_key_inputs():
    html = _signoff_html()
    assert 'id="key"' not in html, "API Key 입력칸이 남아 있다"
    assert 'id="reviewer"' not in html, "검수자 이름 입력칸이 남아 있다"
    assert 'id="role"' not in html, "역할 선택칸이 남아 있다"
    assert "X-API-Key" not in html
    assert "X-Actor-Role" not in html
    # 프리필 자리표시자가 치환되지 않고 남아 있으면 화면에 그대로 보인다.
    assert "__APIKEY_DEFAULT__" not in html and "__REVIEWER_DEFAULT__" not in html


def test_signoff_page_reads_identity_from_login_cookie():
    html = _signoff_html()
    assert "/api/v1/golden/candidates/session" in html, "신원을 서버에 묻지 않는다"
    assert "credentials:'same-origin'" in html, "쿠키를 붙이지 않으면 인증이 안 된다"
    assert 'id="who"' in html, "서명자를 화면에 보여주지 않으면 누가 서명하는지 모른다"


def test_signoff_page_sends_authenticated_identity_in_body():
    """본문 actor 는 화면이 지어낸 값이 아니라 /session 이 준 값이어야 한다."""
    html = _signoff_html()
    assert "actor:{user_id:WHO,role:WHOROLE}" in html


# ── 2. 프리필 인자가 되돌아오지 못한다 ────────────────────────────────────────

@pytest.mark.parametrize("kw", ["default_reviewer", "default_api_key"])
def test_renderer_no_longer_accepts_identity_prefill(kw):
    """인자가 살아 있으면 호출부 한 줄로 프리필이 부활한다 — 시그니처에서 없앤다."""
    assert kw not in inspect.signature(render_signoff_html).parameters
    with pytest.raises(TypeError):
        render_signoff_html(_RECORDS, job_id="j", post_url="/p", **{kw: "x"})


def test_signoff_page_never_embeds_the_shared_api_key(monkeypatch):
    """공유 키가 페이지 본문에 박혀 나가면 서명 링크를 받은 사람 모두가 관리자 키를 얻는다.

    실측(223, 2026-08-17): .env.jjw 에 SIGNOFF_PREFILL_API_KEY=1 이 켜져 있었다.
    """
    from koipa import config as cfg
    monkeypatch.setattr(cfg.settings, "api_key", "SUPER-SECRET-SHARED-KEY", raising=False)
    monkeypatch.setattr(cfg.settings, "signoff_prefill_api_key", True, raising=False)
    assert "SUPER-SECRET-SHARED-KEY" not in _signoff_html()


# ── 3·4. 서버가 신원을 확정하는 규칙 ──────────────────────────────────────────

def test_jwt_subject_overrides_client_supplied_reviewer_name():
    claims = JWTClaims(sub="hong.gd", roles=("reviewer",), exp=9999999999)
    effective, overridden = resolve_actor_user_id("someone.else", {"mode": "jwt", "claims": claims})
    assert effective == "hong.gd"
    assert overridden is True, "덮어썼다는 신호가 없으면 위조 시도가 조용히 지나간다"


def test_shared_api_key_cannot_prove_who_signed():
    """이 동작은 '고치는' 대상이 아니라 **화면에서 키 입력칸을 없애야 하는 이유**다.

    공유 키에는 개별 신원이 없어 서버가 확정할 값이 없다. 그래서 브라우저가 그 경로를
    쓸 수 없게 만드는 것이 유일한 차단이다(위 test_signoff_page_has_no_identity_or_key_inputs).
    """
    effective, overridden = resolve_actor_user_id("자칭.이름", {"mode": "api_key", "actor_role": "admin"})
    assert effective == "자칭.이름" and overridden is False


# ── 5. reviewer 가 자기 신원을 읽을 수 있다 ───────────────────────────────────

def _session_role_check():
    return inspect.signature(proxy_gold_candidate_session).parameters["auth"].default.dependency


@pytest.mark.parametrize("role", ["admin", "reviewer", "kl_backend"])
def test_session_endpoint_allows_every_role_that_may_sign(role):
    """서명 API 가 받는 역할은 전부 자기 신원을 읽을 수 있어야 한다.

    reviewer 가 빠져 있으면 검수자 화면이 신원 조회 403 으로 서명 버튼까지 막힌다.
    """
    asyncio.run(_session_role_check()({"actor_roles": (role,), "actor_role": role}))


def test_session_endpoint_still_rejects_unlisted_role():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        asyncio.run(_session_role_check()({"actor_roles": ("system",), "actor_role": "system"}))
    assert e.value.status_code == 403


def test_session_returns_authenticated_role_usable_as_actor_role():
    claims = JWTClaims(sub="hong.gd", roles=("reviewer",), exp=9999999999)
    out = proxy_gold_candidate_session({"mode": "jwt", "claims": claims, "actor_role": "reviewer"})
    assert out["actor_id"] == "hong.gd" and out["actor_role"] == "reviewer"
    # 화면이 이 값을 본문 actor.role 로 그대로 싣는다 — 스키마 패턴을 통과해야 한다.
    Actor(user_id=out["actor_id"], role=out["actor_role"])


def test_every_valid_role_passes_the_actor_schema_pattern():
    """/session 이 돌려줄 수 있는 역할 전부가 본문 스키마를 통과하는지 — 하나라도 어긋나면 422."""
    for r in sorted(VALID_ROLES):
        Actor(user_id="x", role=r)


def test_signoff_page_points_at_login_when_there_is_no_session():
    """링크(?t=)는 화면을 여는 열쇠일 뿐 신원이 아니다.

    실측(223, 2026-08-17): signoff.html?t=... 는 200 으로 열린다. 그런데 로그인 쿠키가
    없으면 서명은 못 한다 — 화면이 갈 곳을 안 알려주면 검수자는 '열리는데 왜 못 누르나'
    에서 멈춘다. 그 자리에서 로그인 경로를 준다.
    """
    html = _signoff_html()
    assert "/api/v1/golden/candidates/login.html" in html, "로그인 경로가 화면에 없다"
    assert "새로고침" in html, "로그인 뒤 무엇을 해야 하는지 안내가 없다"
    # 401·403 을 구분해 처리해야 '실패' 한 단어로 뭉뚱그려지지 않는다.
    assert "r.status===401||r.status===403" in html
