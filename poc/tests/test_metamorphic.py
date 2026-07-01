"""metamorphic (D 코어) — 사실 보존 게이트 + 대칭 메타모픽 채점(실패만 정보) 테스트."""
from __future__ import annotations

import pytest

from lloydk.modules.m6_evaluation.metamorphic import (
    build_metamorphic_report,
    check_fact_preservation,
)


# ---- 사실 보존 게이트 (D 라벨상속 결정적 검사) ----

def test_preservation_admits_when_tokens_kept():
    text = "본 자료는 EUV 공정 레시피와 국가핵심기술 소분류에 해당하는 설계 명세이다."
    res = check_fact_preservation(text, ["EUV 공정 레시피", "국가핵심기술"])
    assert res.admit
    assert res.reason == "ok"


def test_preservation_rejects_missing_token():
    text = "본 자료는 일반 공정 설명서이다."
    res = check_fact_preservation(text, ["EUV 공정 레시피", "국가핵심기술"])
    assert not res.admit
    assert "EUV 공정 레시피" in res.missing_tokens


def test_preservation_rejects_negated_token():
    # 토큰은 살아있지만 부정 문맥에 묶임 → 사실 반전 → 거부
    text = "본 기술은 국가핵심기술에 해당하지 않는 일반 기술이다."
    res = check_fact_preservation(text, ["국가핵심기술"])
    assert not res.admit
    assert "국가핵심기술" in res.negated_tokens


def test_preservation_empty_required_is_not_admit():
    res = check_fact_preservation("아무 텍스트", [])
    assert not res.admit
    assert "검사 불가" in res.reason


# ---- 메타모픽 대칭 채점 ----

def test_forward_violation_is_high_grade_miss():
    pairs = [
        {"anchor_id": "a1", "anchor_grade": "TS", "paraphrase_pred": "TS"},  # ok
        {"anchor_id": "a2", "anchor_grade": "TS", "paraphrase_pred": "S3"},  # 위반(미탐)
        {"anchor_id": "a3", "anchor_grade": "S1", "paraphrase_pred": "S2"},  # 위반(미탐)
    ]
    rep = build_metamorphic_report(pairs)
    assert rep.forward.n == 3
    assert rep.forward.violations == 2
    ids = {f["anchor_id"] for f in rep.forward_failures}
    assert ids == {"a2", "a3"}


def test_reverse_violation_when_grade_does_not_drop():
    # 등급 결정 사실 제거 후 등급이 안 내려가면 = 과분류(표면 단서 패닉)
    rev = [
        {"anchor_grade": "TS", "fact_removed_pred": "S3"},  # 내려감 → ok
        {"anchor_grade": "TS", "fact_removed_pred": "TS"},  # 안 내려감 → 위반
        {"anchor_grade": "S1", "fact_removed_pred": "TS"},  # 더 비밀 → 위반
    ]
    rep = build_metamorphic_report([], reverse_pairs=rev)
    assert rep.reverse.n == 3
    assert rep.reverse.violations == 2


def test_overclass_public_should_stay_s3():
    pub = [
        {"pred": "S3"},  # ok
        {"pred": "S2"},  # 위반(공개를 비밀로)
        {"pred": "TS"},  # 위반
    ]
    rep = build_metamorphic_report([], public_records=pub)
    assert rep.over_class.n == 3
    assert rep.over_class.violations == 2


def test_clean_forward_has_zero_violations_but_no_quality_claim():
    pairs = [{"anchor_grade": "TS", "paraphrase_pred": "TS"} for _ in range(20)]
    rep = build_metamorphic_report(pairs)
    assert rep.forward.violations == 0  # 위반 0
    # 그러나 '품질 좋음' 점수는 코드 레벨에서 금지 (실패만 정보)
    with pytest.raises(NotImplementedError):
        _ = rep.quality_score


def test_to_dict_has_no_quality_score():
    rep = build_metamorphic_report([{"anchor_grade": "TS", "paraphrase_pred": "TS"}])
    d = rep.to_dict()
    assert d["quality_score"] is None
    assert "실패만 정보" in d["note"]


def test_violation_ci_is_valid_interval():
    pairs = [{"anchor_grade": "TS", "paraphrase_pred": "S3"}] * 3
    pairs += [{"anchor_grade": "TS", "paraphrase_pred": "TS"}] * 7
    rep = build_metamorphic_report(pairs)
    lo, hi = rep.forward.ci
    assert 0.0 <= lo <= rep.forward.violation_rate <= hi <= 1.0
