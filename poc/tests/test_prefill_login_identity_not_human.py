"""로그인 화면이 나눠 주는 신원으로도 **서명할 수 있다**(2026-08-20 사용자 결정).

이 파일은 2026-08-17 에 정반대를 잠그고 있었다 — 프리필 신원은 사람 검수자가 아니라고
보고 서명을 막았다. 근거는 이랬다.

    콘솔 노출        0.0.0.0:8000 (외부 접속 가능)
    login.html      무인증 페이지
    페이지 본문      sub=kl-admin-test · roles=[admin] JWT 가 그대로 박혀 있다
    → 223:8000 에 닿는 누구나 토큰을 받아 서명할 수 있다

그 규칙이 실제로 한 일은 **검수를 0건으로 만든 것**이었다. 223 preflight 가
`reviewer_rejected` 로 막아 후보 120건 중 한 건도 서명되지 않았다. 대안(사람마다 토큰
발급 → 브라우저마다 붙여넣기)은 시연 도중 로그인이 막히는 위험을 만든다.

사용자 결정: **로그인은 항상 되어야 한다.** 그래서 프리필 신원 거부를 뺐다.

⚠ 무엇을 잃는지 분명히 한다 — 원장에 **사람별 구분이 남지 않는다.** 그 주소를 여는
  누구나 같은 이름으로 서명하므로 `reviewer_id` 는 "이 조직이 검수했다" 까지만 말한다.
  사람별 추적이 필요해지면 프리필을 끄고 `scripts/setup_console_test_login.py --sub` 로
  사람마다 발급하는 방식으로 되돌려야 한다.

⚠ 그래서 **프리필 이름은 실계정 이름이어야 한다.** 기계·시연 접두사 검사는 그대로 살아
  있어서, `demo-*` 같은 이름을 프리필에 넣으면 여전히 서명이 막힌다 — 그 이름이 원장에
  남으면 검수 기록으로 쓸 수 없기 때문이다. 아래 시험이 그 경계를 잠근다.
"""

from __future__ import annotations

import base64
import json

import pytest

from koipa.config import settings
from koipa.golden_tiers import _prefill_login_subject, is_human_reviewer


def _fake_token(sub: str) -> str:
    """서명은 필요 없다 — 읽는 것은 '우리가 화면에 뿌리는 이름' 이지 토큰의 진위가 아니다."""
    def b64(o: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(o).encode()).rstrip(b"=").decode()
    return f"{b64({'alg': 'RS256'})}.{b64({'sub': sub, 'roles': ['admin']})}.sig"


@pytest.fixture
def prefill(monkeypatch):
    def _set(sub: str | None):
        monkeypatch.setattr(
            settings, "console_login_prefill_token",
            _fake_token(sub) if sub else "", raising=False,
        )
    return _set


def test_prefill_subject_is_still_readable(prefill):
    """이름을 읽는 기능 자체는 남긴다 — 화면·진단이 쓰고, 되돌릴 때도 필요하다."""
    prefill("지재원관리자")
    assert _prefill_login_subject() == "지재원관리자"


def test_shared_login_identity_can_sign(prefill):
    """핵심 — URL 만 열면 검수·서명이 되어야 한다."""
    prefill("지재원관리자")
    assert is_human_reviewer("지재원관리자") is True


def test_real_accounts_still_pass(prefill):
    prefill("지재원관리자")
    assert is_human_reviewer("hong.gd") is True
    assert is_human_reviewer("kim.cs") is True


def test_no_prefill_changes_nothing(prefill):
    """프리필 유무가 실계정 판정을 흔들면 안 된다."""
    prefill(None)
    assert _prefill_login_subject() == ""
    assert is_human_reviewer("hong.gd") is True


@pytest.mark.parametrize("bad", ["", "not-a-jwt", "a.b", "a.!!!.c", "a." + "x" * 8 + ".c"])
def test_broken_token_does_not_crash_the_gate(monkeypatch, bad):
    """설정이 깨졌다고 검수 판정 전체가 예외로 죽으면 안 된다."""
    monkeypatch.setattr(settings, "console_login_prefill_token", bad, raising=False)
    assert _prefill_login_subject() == ""
    assert is_human_reviewer("hong.gd") is True


def test_machine_and_demo_names_are_still_refused(prefill):
    """프리필을 열어 줬다고 시연용 이름까지 통과시키면 원장이 못 쓰게 된다.

    `demo-console` 이 막히는 것이 이 경계다 — 프리필에 그런 이름을 넣으면 서명이 막히므로,
    운영자는 실계정 이름을 넣을 수밖에 없다.
    """
    prefill("demo-console")
    assert is_human_reviewer("demo-console") is False
    assert is_human_reviewer("ai_assist") is False
    assert is_human_reviewer("") is False
