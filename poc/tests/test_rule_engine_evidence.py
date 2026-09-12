"""rule_engine.has_real_evidence — 시드(start=None) vs 영어 약어 부스트(start=정수) 판별자 고정.

이 규약이 깨지면(예: 향후 시드 매칭이 start를 채우면) 합의 게이트의 '실제 근거' 판정이
조용히 뒤집혀 약어 단독 문서가 gold_candidate가 될 수 있다 → 이 테스트가 알려준다.
"""
from koipa.modules.m3_labeling.rule_engine import (
    MatchedKeyword,
    RuleLabelResult,
    has_real_evidence,
)


def _mk(start):
    return MatchedKeyword(
        keyword="x", grade="S1", factor="SECRECY", count=1, weight=1.0, score=1.0, start=start,
    )


def _result(mks):
    return RuleLabelResult(
        grade="S1", confidence=1.0, grade_scores={}, factor_scores={},
        matched_keywords=mks, total_score=1.0,
    )


def test_seed_match_is_real_evidence():
    # 시드 매칭은 start=None → 실제 근거.
    assert has_real_evidence(_result([_mk(start=None)])) is True


def test_acronym_only_not_real_evidence():
    # 영어 약어 부스트만(start 채워짐) → 근거 아님.
    assert has_real_evidence(_result([_mk(start=5)])) is False


def test_mixed_is_real_evidence():
    # 약어 + 시드 혼합 → 시드가 있으므로 근거 있음.
    assert has_real_evidence(_result([_mk(start=5), _mk(start=None)])) is True


def test_no_match_not_real_evidence():
    assert has_real_evidence(_result([])) is False
