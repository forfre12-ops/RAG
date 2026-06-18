"""M3 Labeling — 룰엔진·시드·파이프라인."""

from __future__ import annotations

import pytest

from lloydk.adapters.embedding import HashEmbedding
from lloydk.modules.m3_labeling import (
    FACTOR_SEEDS,
    GRADE_ORDER,
    KEYWORD_SEEDS,
    LabelingPipeline,
    LabelRuleEngine,
)


def test_canonical_factors_are_three_requisites():
    """정본 B안 — 평가요소는 3요건(S·V·M). 곱셈식이라 가산 가중치 합 제약 없음."""
    codes = {f["code"] for f in FACTOR_SEEDS}
    assert codes == {"SECRECY", "VALUE", "MANAGEMENT"}


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


@pytest.mark.slow
def test_pipeline_returns_evaluation_factors():
    pipe = LabelingPipeline()
    out = pipe.label("1급 비밀 영업비밀 공정 노하우 원가 구조")
    assert hasattr(out.factors, "secrecy")
    assert hasattr(out.factors, "value")
    assert hasattr(out.factors, "management")
    # 적어도 한 factor는 점수가 잡혀야 함
    total = out.factors.secrecy + out.factors.value + out.factors.management
    assert total > 0.0


@pytest.mark.slow
def test_pipeline_evidence_extracted():
    pipe = LabelingPipeline()
    out = pipe.label("특급기밀 차세대 제품 설계도")
    assert len(out.evidence) > 0
    texts = [e.text for e in out.evidence]
    assert "특급기밀" in texts or "차세대 제품 설계도" in texts


@pytest.mark.slow
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


# ============================================================
# semantic pattern_type (임베딩 코사인 매칭) — placeholder 해소
# ============================================================
def test_semantic_seed_matches_identical_text():
    """hash 임베딩은 동일 텍스트면 코사인=1.0 → semantic 매칭 성공."""
    emb = HashEmbedding(dim=256)
    eng = LabelRuleEngine(
        seeds=[
            {
                "grade": "TS",
                "keyword": "기밀 설계 도면",
                "weight": 1.0,
                "factor": "ECONOMIC_VALUE",
                "pattern_type": "semantic",
            },
        ],
        embedder=emb,
        semantic_threshold=0.75,
    )
    out = eng.label("기밀 설계 도면")
    assert out.grade == "TS"
    assert any(m.keyword == "기밀 설계 도면" for m in out.matched_keywords)
    # semantic은 빈도가 없으므로 count는 정확히 1
    assert all(m.count == 1 for m in out.matched_keywords if m.keyword == "기밀 설계 도면")


def test_semantic_seed_rejects_unrelated_text():
    """완전히 다른 토큰 집합이면 hash 임베딩 코사인이 낮아 미매칭."""
    emb = HashEmbedding(dim=256)
    eng = LabelRuleEngine(
        seeds=[
            {
                "grade": "TS",
                "keyword": "반도체 공정 레시피",
                "weight": 1.0,
                "factor": "ECONOMIC_VALUE",
                "pattern_type": "semantic",
            },
        ],
        embedder=emb,
        semantic_threshold=0.75,
    )
    out = eng.label("점심 식단 안내 김치찌개")
    # 키워드 매칭이 0이면 기본 S3
    assert out.grade == "S3"
    assert out.matched_keywords == []


def test_semantic_seed_cache_avoids_recomputation():
    """동일 seed.value 재평가 시 임베딩 캐시 활용 — _seed_vec_cache에 한 번만 저장."""
    emb = HashEmbedding(dim=128)
    eng = LabelRuleEngine(
        seeds=[
            {
                "grade": "S1",
                "keyword": "내부 API 키",
                "weight": 1.0,
                "factor": "MANAGEMENT_LEVEL",
                "pattern_type": "semantic",
            },
        ],
        embedder=emb,
        semantic_threshold=0.75,
    )
    # 같은 텍스트로 두 번 label 호출
    eng.label("내부 API 키")
    eng.label("내부 API 키")
    # seed.value별로 캐시 항목이 정확히 1개
    assert "내부 API 키" in eng._seed_vec_cache
    assert len(eng._seed_vec_cache) == 1


def test_semantic_threshold_environment_override(monkeypatch):
    """EMB_SEMANTIC_THRESHOLD 환경변수로 임계값 오버라이드."""
    monkeypatch.setenv("EMB_SEMANTIC_THRESHOLD", "0.99")
    eng = LabelRuleEngine(embedder=HashEmbedding(dim=64))
    assert abs(eng.semantic_threshold - 0.99) < 1e-9


def test_exact_and_regex_paths_unchanged():
    """semantic 도입이 기존 exact/regex 매칭을 깨뜨리지 않는지 보증."""
    eng = LabelRuleEngine(
        seeds=[
            {"grade": "TS", "keyword": "특급기밀", "weight": 1.0, "factor": "LEAK_IMPACT"},  # exact (기본)
            {
                "grade": "S1",
                "keyword": r"비밀\s*문서",
                "weight": 0.9,
                "factor": "NON_PUBLICITY",
                "pattern_type": "regex",
            },
        ],
    )
    out = eng.label("이 문서는 특급기밀이며 비밀 문서로 분류된다.")
    matched_kw = {m.keyword for m in out.matched_keywords}
    assert "특급기밀" in matched_kw
    assert any("비밀" in kw for kw in matched_kw)
    assert out.grade == "TS"
