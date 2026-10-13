"""시연 마커 서명이 사람 검수로 집계되지 않는가 — 평가정답 무결성 회귀 방어.

배경(2026-08-08 실측): 시연 드라이버(scripts/demo_e2e_golden.py)와 화면 경로
(분류 콘솔의 #sec-parse 구역)는 actor.user_id 로 'demo-console' 을 쓴다(admin.DEMO_CREATED_BY).
그 값으로 골든 서명을 하면 label_source='human_review' · reviewer_id='demo-console' 인
레코드가 나오는데, is_human_reviewer 의 머신 접두 목록에 'demo' 가 없어서 이것이
사람 서명으로 통과했고 tier 가 locked_gold_eval 로 잡혔다.

locked_gold_eval 은 '사람이 서명한 평가 정답'이라는 뜻이고 배포 게이트가 그것을 근거로
자동 활성화를 연다. 시연·리허설 산출물이 여기 섞이면 게이트가 검증되지 않은 모델을
검증된 것으로 오판한다. 실제로 --publish 를 주면 라이브 경로에 병합되는 길이 열려 있다.
"""
from __future__ import annotations

import pytest

from koipa.golden_tiers import (
    SUPPORTED_GATE_VERSIONS,
    TIER_HELD,
    is_human_reviewer,
    is_valid_signoff,
    tier_of,
)


@pytest.mark.parametrize(
    "reviewer_id",
    ["demo-console", "demo_console", "DEMO-CONSOLE", "demo-user", "demo"],
)
def test_demo_marker_is_not_a_human_reviewer(reviewer_id):
    assert is_human_reviewer(reviewer_id) is False


@pytest.mark.parametrize("reviewer_id", ["admin@koipa", "reviewer-kim", "hong.gildong"])
def test_real_accounts_still_pass(reviewer_id):
    """오탐 방지 — 실계정은 그대로 통과해야 한다."""
    assert is_human_reviewer(reviewer_id) is True


def _signed_record(reviewer_id: str) -> dict:
    """서명 envelope 를 갖춘 레코드 — reviewer_id 만 바꿔 판정을 가른다.

    gate_version 은 빌드 게이트(v2_agreement_evidence)가 아니라 **서명 게이트**
    (SUPPORTED_GATE_VERSIONS = human_signoff_v1)를 넣어야 한다. 둘은 다른 축이고
    is_valid_signoff 는 후자만 인정한다.
    """
    return {
        "doc_id": "d1",
        "text": "본문",
        "label": "S1",
        "label_source": "human_review",
        "reviewer_id": reviewer_id,
        "reviewer_ids": [reviewer_id],
        "gate_version": next(iter(SUPPORTED_GATE_VERSIONS)),
        "signed_at": "2026-08-08T00:00:00Z",
        "source": "판례",
    }


def test_demo_signoff_does_not_become_locked_eval():
    """시연 서명은 평가 정답이 아니라 격리(held_review)로 가야 한다."""
    rec = _signed_record("demo-console")
    assert is_valid_signoff(rec) is False
    assert tier_of(rec) == TIER_HELD


def test_real_signoff_still_becomes_locked_eval():
    """실계정 서명은 종전대로 평가 정답으로 승격된다(과교정 방지)."""
    rec = _signed_record("admin@koipa")
    assert is_valid_signoff(rec) is True
    assert tier_of(rec) != TIER_HELD


# ── 2026-08-17: 설정 기본 검수자 · 일괄 서명 ────────────────────────────────
#
# KL 서버 실측에서 SIGNOFF_DEFAULT_REVIEWER=hong.gildong 이 설정돼 있었고,
# locked_gold_eval 20건이 전원 그 이름 · 19건이 동일 마이크로초 서명이었다.
# 화면에 자동으로 채워지는 이름은 사람이 바꾸지 않으면 누가 검수했든 같은 값이 남는다.
# 이름의 실존 여부와 무관하게 "기본값이 만든 서명"은 개별 검수 행위가 아니다.


def test_configured_default_reviewer_is_not_a_human_reviewer(monkeypatch):
    from koipa.config import settings

    monkeypatch.setattr(settings, "signoff_default_reviewer", "hong.gildong", raising=False)
    assert is_human_reviewer("hong.gildong") is False
    assert is_human_reviewer("HONG.GILDONG") is False  # 대소문자 무관
    # 기본값이 아닌 다른 실계정은 그대로 통과해야 한다(오탐 방지)
    assert is_human_reviewer("reviewer-kim") is True


def test_default_reviewer_unset_keeps_previous_behavior(monkeypatch):
    from koipa.config import settings

    monkeypatch.setattr(settings, "signoff_default_reviewer", "", raising=False)
    assert is_human_reviewer("hong.gildong") is True


def test_batch_signed_groups_flags_identical_timestamps():
    from koipa.golden_tiers import batch_signed_groups

    ts = "2026-08-06T14:42:14.904855+00:00"
    records = [{"signed_at": ts} for _ in range(19)] + [{"signed_at": "2026-08-07T14:41:06+00:00"}]
    groups = batch_signed_groups(records)
    assert groups == {ts: 19}, "동일 마이크로초 19건이 일괄 서명으로 잡혀야 한다"


def test_batch_signed_groups_clean_when_each_signature_is_distinct():
    from koipa.golden_tiers import batch_signed_groups

    records = [{"signed_at": f"2026-08-06T14:4{i}:14.90{i}855+00:00"} for i in range(5)]
    assert batch_signed_groups(records) == {}
