"""serving_invariants (E) — 출처 cap 조용한 미탐 + floor 위반 체커 테스트."""
from __future__ import annotations

import pytest

from lloydk.modules.m6_evaluation.serving_invariants import (
    ServingCase,
    build_serving_invariant_report,
)


def test_silent_cap_detected_when_no_review_signal():
    # 공개 출처가 TS 콘텐츠를 S3로 cap했는데 검수 신호 없음 → 조용한 미탐(위반)
    cases = [ServingCase(content_grade="TS", final_grade="S3", source_public=True, has_review_signal=False)]
    rep = build_serving_invariant_report(cases)
    assert rep.silent_cap.n == 1
    assert rep.silent_cap.violations == 1
    assert rep.silent_cap_failures[0]["content_grade"] == "TS"


def test_cap_with_review_signal_is_not_violation():
    # 같은 cap이지만 cap-conflict/needs_review 신호가 있으면 가드가 작동한 것 → 위반 아님
    cases = [ServingCase(content_grade="TS", final_grade="S3", source_public=True, has_review_signal=True)]
    rep = build_serving_invariant_report(cases)
    assert rep.silent_cap.n == 1
    assert rep.silent_cap.violations == 0


def test_non_public_source_not_counted_as_silent_cap():
    cases = [ServingCase(content_grade="TS", final_grade="S3", source_public=False, has_review_signal=False)]
    rep = build_serving_invariant_report(cases)
    assert rep.silent_cap.n == 0  # 공개 출처 아님 → 이 불변식 대상 아님


def test_s2_public_cap_not_silent_miss():
    # S2 콘텐츠를 공개출처로 S3 cap = 의도된 동작(고등급 아님) → silent_cap 대상 아님
    cases = [ServingCase(content_grade="S2", final_grade="S3", source_public=True, has_review_signal=False)]
    rep = build_serving_invariant_report(cases)
    assert rep.silent_cap.n == 0


def test_floor_violation_when_marking_higher_than_final():
    # security_marking=TS인데 최종 S2 → 상향(floor) 누락 → 위반
    cases = [ServingCase(content_grade="S2", final_grade="S2", marking_grade="TS")]
    rep = build_serving_invariant_report(cases)
    assert rep.floor.n == 1
    assert rep.floor.violations == 1


def test_floor_ok_when_final_already_higher_or_equal():
    cases = [
        ServingCase(content_grade="TS", final_grade="TS", marking_grade="S2"),  # 최종이 이미 더 비밀 → ok
        ServingCase(content_grade="S1", final_grade="S1", marking_grade="S1"),  # 같음 → ok
    ]
    rep = build_serving_invariant_report(cases)
    assert rep.floor.n == 2
    assert rep.floor.violations == 0


def test_accepts_dict_records():
    cases = [{"content_grade": "S1", "final_grade": "S3", "source_public": True, "has_review_signal": False}]
    rep = build_serving_invariant_report(cases)
    assert rep.silent_cap.violations == 1


def test_quality_score_forbidden():
    rep = build_serving_invariant_report([ServingCase(content_grade="TS", final_grade="TS")])
    with pytest.raises(NotImplementedError):
        _ = rep.quality_score


def test_to_dict_has_no_quality_score():
    rep = build_serving_invariant_report([ServingCase(content_grade="TS", final_grade="S3", source_public=True)])
    d = rep.to_dict()
    assert d["quality_score"] is None
    assert "실패만 정보" in d["note"]


def test_violation_ci_valid():
    cases = [ServingCase(content_grade="TS", final_grade="S3", source_public=True, has_review_signal=False)] * 2
    cases += [ServingCase(content_grade="TS", final_grade="S3", source_public=True, has_review_signal=True)] * 8
    rep = build_serving_invariant_report(cases)
    lo, hi = rep.silent_cap.ci
    assert 0.0 <= lo <= rep.silent_cap.violation_rate <= hi <= 1.0
