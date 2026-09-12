"""거부 사유를 말해 준다 — 그리고 한글 실계정 이름이 전 구간을 통과한다.

왜(2026-08-17). 거부 조건이 다섯 갈래인데 응답은 "실계정이어야 합니다" 한 문장이었다.
뒤 두 갈래(화면 기본값 이름 · 로그인 프리필 공용 신원)는 **이름 자체에는 문제가 없고
설정 때문에** 막히는 것이라, 이유가 없으면 이름만 계속 바꿔 보게 된다.

실제 함정: 검수자 토큰을 CONSOLE_LOGIN_PREFILL_TOKEN 에 넣으면 그 순간 그 이름이 공용
신원으로 분류돼 그 사람의 **모든 서명이 403** 이 된다. 사유가 없으면 원인을 못 찾는다.
"""

from __future__ import annotations

import base64
import json

import pytest

from koipa.config import settings
from koipa.golden_tiers import human_reviewer_rejection_reason as why
from koipa.golden_tiers import is_human_reviewer
from koipa.schemas.common import Actor

# 실서비스 전 검수 신원(사용자 지정 2026-08-17). 한글이 사슬 전체를 통과해야 한다.
INTERIM = "지재원관리자"


@pytest.fixture(autouse=True)
def _settings(monkeypatch):
    def _tok(sub: str) -> str:
        b = base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode()).rstrip(b"=").decode()
        return f"h.{b}.s"
    monkeypatch.setattr(settings, "signoff_default_reviewer", "hong.gildong", raising=False)
    monkeypatch.setattr(settings, "console_login_prefill_token", _tok("kl-admin-test"),
                        raising=False)


# ── 사유가 원인을 짚는다 ──────────────────────────────────────────────────────

@pytest.mark.parametrize("name,must_mention", [
    ("hong.gildong", "SIGNOFF_DEFAULT_REVIEWER"),
])
def test_config_driven_rejections_name_the_setting(name, must_mention):
    """이름이 아니라 설정 때문에 막힌 경우 — 어느 설정인지 말해야 고칠 수 있다."""
    r = why(name)
    assert r and must_mention in r


def test_prefill_identity_is_allowed_to_sign():
    """[2026-08-20 사용자 결정] 로그인은 항상 되어야 한다 — 프리필 신원 거부를 뺐다.

    종전에는 `kl-admin-test`(프리필 sub)를 막았고, 그 때문에 223 에서 후보 120건이
    한 건도 서명되지 않았다. 잃는 것은 원장의 사람별 구분이다 —
    [[test_prefill_login_identity_not_human]] 에 경위를 적어 뒀다.
    """
    assert why("kl-admin-test") == ""
    assert is_human_reviewer("kl-admin-test") is True


@pytest.mark.parametrize("name", ["", "reviewer", "ai_assist", "demo-console", "llm_judge_1"])
def test_rejected_names_always_carry_a_reason(name):
    assert why(name), f"{name!r} 이 거부되는데 사유가 비어 있다"


def test_reason_matches_the_boolean_gate_exactly():
    """사유 함수와 판정 함수가 어긋나면 '통과인데 사유가 있다' 같은 상태가 생긴다."""
    for n in ("", "reviewer", "ai_assist", "AI_assist", "demo-console", "kl-admin-test",
              "hong.gildong", "kim.cs", INTERIM, "llm_judge_1", "bot_x", "aiden"):
        assert is_human_reviewer(n) == (not why(n)), n


# ── 한글 실계정이 사슬 전체를 통과한다 ────────────────────────────────────────

def test_interim_korean_reviewer_is_accepted():
    assert why(INTERIM) == ""
    assert is_human_reviewer(INTERIM) is True


def test_interim_name_passes_the_actor_schema():
    Actor(user_id=INTERIM, role="admin")
    Actor(user_id=INTERIM, role="reviewer")


def test_interim_name_survives_jwt_payload_encoding():
    """JWT payload 는 ensure_ascii 로 \\uXXXX 이 된다 — 되돌아와야 원장에 제 이름이 남는다."""
    raw = json.dumps({"sub": INTERIM}, separators=(",", ":"))
    assert "\\u" in raw, "비-ASCII 가 이스케이프되지 않으면 base64 경로에서 깨질 수 있다"
    b = base64.urlsafe_b64encode(raw.encode()).rstrip(b"=").decode()
    b += "=" * (-len(b) % 4)
    assert json.loads(base64.urlsafe_b64decode(b))["sub"] == INTERIM


def test_interim_name_is_safe_as_a_token_filename():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "scripts" / "setup_console_test_login.py"
    spec = importlib.util.spec_from_file_location("_setup_console_login_for_name", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._safe_name(INTERIM) == INTERIM


def test_reviewer_name_in_the_prefill_now_works(monkeypatch):
    """이 조합이 **정상 경로**가 됐다(2026-08-20).

    종전에는 검수자 이름을 프리필에 넣으면 그 사람의 모든 서명이 403 이었다. 지금은
    그 방식이 기본이다 — URL 만 열면 로그인되고 그대로 서명된다. 잃는 것(원장의 사람별
    구분)은 [[test_prefill_login_identity_not_human]] 에 적었다.
    """
    def _tok(sub: str) -> str:
        b = base64.urlsafe_b64encode(json.dumps({"sub": sub}).encode()).rstrip(b"=").decode()
        return f"h.{b}.s"
    monkeypatch.setattr(settings, "console_login_prefill_token", _tok(INTERIM), raising=False)
    assert why(INTERIM) == ""
    assert is_human_reviewer(INTERIM) is True
