"""로그인 화면이 나눠 주는 공용 신원은 사람 검수자가 아니다.

왜(실측 2026-08-17, KL 223).

    콘솔 노출        0.0.0.0:8000 (외부 접속 가능 — 이 개발 PC 에서 열렸다)
    login.html      무인증 페이지
    페이지 본문      sub=kl-admin-test · roles=[admin] JWT 가 그대로 박혀 있다
    is_human_reviewer("kl-admin-test")  -> True   ← 고치기 전

즉 **223:8000 에 닿을 수 있는 누구나** 토큰을 받아 서명할 수 있고, 그 서명이 사람 검수로
집계됐다. signoff_default_reviewer 를 거부하는 것과 정확히 같은 이유다 — 화면이 준 이름은
개별 검수 행위가 아니다.

⚠ 이 검사는 설정이 있을 때만 작동한다. 운영 배포는 CONSOLE_LOGIN_PREFILL_TOKEN 이 비어
  있으므로(그래야 한다) 거부 대상이 없다. 즉 이 규칙은 **시연·테스트 서버용 안전장치**다.
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


def test_prefill_subject_is_read_from_the_configured_token(prefill):
    prefill("kl-admin-test")
    assert _prefill_login_subject() == "kl-admin-test"


def test_shared_login_identity_is_not_a_human_reviewer(prefill):
    prefill("kl-admin-test")
    assert is_human_reviewer("kl-admin-test") is False
    assert is_human_reviewer("KL-Admin-Test") is False, "대소문자만 바꿔 우회되면 안 된다"


def test_real_accounts_still_pass(prefill):
    prefill("kl-admin-test")
    assert is_human_reviewer("hong.gd") is True
    assert is_human_reviewer("kim.cs") is True


def test_no_prefill_means_no_extra_rejection(prefill):
    """운영 배포는 프리필이 비어 있다 — 이 규칙이 실계정을 잡아먹으면 안 된다."""
    prefill(None)
    assert _prefill_login_subject() == ""
    assert is_human_reviewer("kl-admin-test") is True


@pytest.mark.parametrize("bad", ["", "not-a-jwt", "a.b", "a.!!!.c", "a." + "x" * 8 + ".c"])
def test_broken_token_does_not_crash_the_gate(monkeypatch, bad):
    """설정이 깨졌다고 검수 판정 전체가 예외로 죽으면 안 된다(fail-open 이 아니라 무영향)."""
    monkeypatch.setattr(settings, "console_login_prefill_token", bad, raising=False)
    assert _prefill_login_subject() == ""
    assert is_human_reviewer("hong.gd") is True


def test_machine_rules_still_apply_alongside(prefill):
    prefill("kl-admin-test")
    assert is_human_reviewer("demo-console") is False
    assert is_human_reviewer("ai_assist") is False
    assert is_human_reviewer("") is False
