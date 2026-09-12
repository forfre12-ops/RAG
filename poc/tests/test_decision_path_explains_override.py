# -*- coding: utf-8 -*-
"""decision_path 가 안전 상향의 **원인**을 정확히 말하는가.

왜(2026-08-24, 사용자 지적). 화면에 이렇게 떴다.

    룰 엔진 S2 · 분류기(BERT) S2 · 최종 S1
    결합: rule-override (룰이 S2 강하게 잡아 모델 S2을 안전 상향 → S1)

S2 가 강해서 S1 이 될 수는 없다. 문구가 `rule_grade`(룰의 **최종** 등급)를 찍고 있었는데,
이 상향을 실제로 발동시키는 것은 룰의 최종 등급이 아니라 **등급별 점수**다
(pipeline.run: grade_scores 의 TS/S1/S2 를 각각 임계와 비교).

실측 2026-08-24(LabelRuleEngine, 분기 매출·예산 배정 임원 보고 형태 본문):

    grade_scores = {TS 0.00, S1 2.35, S2 8.65}   → 룰 최종등급(argmax) = S2
    S1 2.35 >= 임계 2.2  → 모델 S2 를 S1 로 상향

즉 argmax 가 S2 여도 S1 점수가 임계를 넘으면 S1 이 된다. 화면은 그 값을 말해야 한다.
"""
from __future__ import annotations

from koipa.services.classify_service import ClassifyService


class _Pred:
    """_decision_path 가 읽는 필드만 가진 최소 대역."""

    def __init__(self, label: str, rule_grade: str | None, model_grade: str | None) -> None:
        self.label = label
        self.rule_grade = rule_grade
        self.model_grade = model_grade


# 실측 재현값 그대로
_OVERRIDE_WARN = "fnr-safe override: rule S1 score=2.35 >= threshold 2.20 (model S2 -> S1)"


def test_override_names_the_grade_that_actually_fired() -> None:
    path = ClassifyService._decision_path(
        _Pred("S1", "S2", "S2"), "needs_review", [_OVERRIDE_WARN, "low_confidence: 0.612 < 0.70"]
    )
    assert "S1 점수 2.35" in path, path
    assert "2.20" in path, "임계값이 없으면 왜 걸렸는지 화면에서 알 수 없다: " + path
    assert "S1 로 안전 상향" in path, path


def test_override_does_not_blame_the_rule_final_grade() -> None:
    """「룰이 S2 강하게 잡아 … → S1」 같은 자기모순 문장이 다시 나오면 안 된다."""
    path = ClassifyService._decision_path(_Pred("S1", "S2", "S2"), "needs_review", [_OVERRIDE_WARN])
    assert "룰이 S2" not in path, path
    # 최종 등급(S1)과 다른 등급을 상향의 주체로 지목하지 않는다.
    assert "룰의 S2" not in path, path


def test_old_warning_format_falls_back_without_inventing_a_cause() -> None:
    """임계가 안 실린 옛 응답을 읽어도 원인을 지어내지 않는다."""
    path = ClassifyService._decision_path(
        _Pred("S1", "S2", "S2"), "needs_review", ["fnr-safe override: rule S1 score=2.4"]
    )
    assert "rule-override" in path
    assert "S2 강하게" not in path, path


def test_other_paths_unchanged() -> None:
    """상향이 아닌 경로는 종전 문구를 그대로 유지한다(회귀 방지)."""
    agree_auto = ClassifyService._decision_path(_Pred("S2", "S2", "S2"), "staging", [])
    assert agree_auto.startswith("agreement"), agree_auto

    agree_review = ClassifyService._decision_path(
        _Pred("S2", "S2", "S2"), "needs_review", ["low_confidence"]
    )
    assert "모두 S2 로 일치" in agree_review, agree_review

    disagree = ClassifyService._decision_path(
        _Pred("S1", "S1", "S2"), "needs_review", ["low_confidence"]
    )
    assert "룰 S1 · 모델 S2 불일치" in disagree, disagree

    rule_only = ClassifyService._decision_path(_Pred("S2", "S2", None), "staging", [])
    assert rule_only.startswith("rule-only"), rule_only


def test_the_grade_scores_really_can_disagree_with_the_rule_final_grade() -> None:
    """근거가 되는 사실 자체를 잠근다 — argmax 가 S2 인데 S1 점수가 임계를 넘는 문서가 있다.

    이 성질이 사라지면 위 문구 수정의 전제가 없어진다. 그때는 문구가 아니라 이 시험이
    먼저 깨져서 알려 준다.
    """
    from koipa.config import settings
    from koipa.modules.m3_labeling.rule_engine import LabelRuleEngine

    text = (
        "분기 매출 · 예산 배정 임원 보고 (대외비)\n"
        "1. 분기 매출 실적 - 월별 매출 추이와 사업 계획 대비 달성률\n"
        "2. 예산 배정안 - 사업부별 예산안, 조직 개편 반영\n"
        "3. 원가 구조 및 원가율 분석\n"
        "4. 자금계획 요약\n"
        "5. 경쟁사 분석 및 시장 조사 결과 참고\n"
        "6. 내부 자료 - 회의록 첨부\n"
    )
    res = LabelRuleEngine().label(text)
    scores = res.grade_scores
    assert res.grade == "S2", f"룰 최종등급이 바뀌었다: {res.grade} · {scores}"
    assert scores["S1"] >= float(settings.fnr_rule_s1_threshold), (
        f"S1 점수 {scores['S1']} 가 임계 {settings.fnr_rule_s1_threshold} 아래다 — "
        f"이 문서로는 더 이상 상향이 재현되지 않는다: {scores}"
    )
    assert scores["S2"] > scores["S1"], f"argmax 가 S1 로 바뀌었다: {scores}"
