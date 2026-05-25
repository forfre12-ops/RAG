"""M3 Labeling — 룰엔진·시드·파이프라인."""

from __future__ import annotations

from lloydk.modules.m3_labeling import (
    FACTOR_SEEDS,
    GRADE_ORDER,
    KEYWORD_SEEDS,
    LabelingPipeline,
    LabelRuleEngine,
)


def test_factor_weights_sum_to_one():
    """DB trigger와 동일 제약 — 가중치 합 ≈ 1.0."""
    s = sum(f["weight"] for f in FACTOR_SEEDS)
    assert abs(s - 1.0) < 0.01


def test_grade_order_has_all_four_levels():
    assert set(GRADE_ORDER) == {"TS", "S1", "S2", "S3"}
    assert GRADE_ORDER["TS"] < GRADE_ORDER["S1"] < GRADE_ORDER["S2"] < GRADE_ORDER["S3"]


def test_keyword_seeds_cover_all_grades():
    grades = {s["grade"] for s in KEYWORD_SEEDS}
    assert grades == {"TS", "S1", "S2", "S3"}


def test_rule_engine_empty_text_defaults_to_s3():
    out = LabelRuleEngine().label("")
    assert out.grade == "S3"
    assert out.confidence == 0.0
    assert "empty" in out.warnings[0].lower()


def test_rule_engine_recognizes_ts():
    text = "특급기밀 자료. 핵심 원천기술과 M&A 계획, 차세대 제품 설계도 포함."
    out = LabelRuleEngine().label(text)
    assert out.grade == "TS"
    assert out.confidence > 0.0
    assert any(m.grade == "TS" for m in out.matched_keywords)


def test_rule_engine_fnr_safe_picks_higher_grade_on_tie():
    """동점일 때 더 높은 등급(낮은 order)을 선택해야 미탐 방지."""
    # TS와 S3 키워드를 동일 가중으로 섞은 텍스트는 보안적으로 TS로 분류
    eng = LabelRuleEngine(
        seeds=[
            {"grade": "TS", "keyword": "AAA", "weight": 1.0, "factor": "LEAK_IMPACT"},
            {"grade": "S3", "keyword": "BBB", "weight": 1.0, "factor": "NON_PUBLICITY"},
        ],
        fnr_safe=True,
    )
    out = eng.label("AAA BBB")
    assert out.grade == "TS"


def test_pipeline_returns_evaluation_factors():
    pipe = LabelingPipeline()
    out = pipe.label("1급 비밀 영업비밀 공정 노하우 원가 구조")
    assert hasattr(out.factors, "economic_value")
    assert hasattr(out.factors, "leak_impact")
    # 적어도 한 factor는 점수가 잡혀야 함
    total = (
        out.factors.economic_value
        + out.factors.non_publicity
        + out.factors.management_level
        + out.factors.leak_impact
    )
    assert total > 0.0


def test_pipeline_evidence_extracted():
    pipe = LabelingPipeline()
    out = pipe.label("특급기밀 차세대 제품 설계도")
    assert len(out.evidence) > 0
    texts = [e.text for e in out.evidence]
    assert "특급기밀" in texts or "차세대 제품 설계도" in texts


def test_pipeline_all_four_grade_scenarios():
    """묶음 D 자가검증의 압축판 — 4 등급 각각 분류 가능 확인."""
    pipe = LabelingPipeline()
    cases = [
        ("TS", "특급기밀 핵심 원천기술 M&A 계획 차세대 제품 설계도"),
        ("S1", "1급 비밀 영업비밀 공정 노하우 원가 구조 고객 데이터베이스"),
        ("S2", "대외비 내부 검토 분기 매출 사업 계획 거래처 명단"),
        ("S3", "보도자료 공시 채용 공고 회사 소개 이용약관"),
    ]
    for truth, text in cases:
        out = pipe.label(text)
        pred = out.grade.value if hasattr(out.grade, "value") else str(out.grade)
        assert pred == truth, f"expected {truth}, got {pred} for {text!r}"
