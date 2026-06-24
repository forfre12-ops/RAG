"""m3_labeling.consensus.evaluate_consensus 합의 게이트 단위 테스트.

make_llm_judge_gold.py에서 추출한 로직의 동작을 고정한다(회귀 방지).
기본 임계: min_rule_conf=0.5, min_llm_conf=0.7, rule_no_opinion<0.05.
"""
from lloydk.modules.m3_labeling.consensus import evaluate_consensus


def test_path_a_consensus():
    # A) 동의 + 둘 다 신뢰도 충족
    r = evaluate_consensus("S1", 0.8, "S1", 0.9)
    assert r.is_gold and r.status == "gold_consensus"
    assert r.label_source == "llm_judge_consensus"
    assert r.review_status == "accepted"


def test_path_b_llm_primary_rule_no_opinion_agree():
    # B) 룰 무의견(conf<0.05) + 동의 + LLM 고신뢰
    r = evaluate_consensus("S3", 0.0, "S3", 0.8)
    assert r.is_gold and r.status == "gold_llm_primary"
    assert r.label_source == "llm_judge_primary"
    assert r.review_status == "accepted"
    assert r.conf_ok is False and r.rule_no_opinion is True


def test_path_c_llm_upper_disagree():
    # C) 룰 무의견 + LLM 상위등급(S1) + 고신뢰 → 불일치여도 gold
    r = evaluate_consensus("S3", 0.0, "S1", 0.85)
    assert r.is_gold and r.status == "gold_llm_upper"
    assert r.label_source == "llm_judge_primary"
    assert r.agree is False


def test_path_c_excludes_s3_llm_only():
    # 룰 무의견 + LLM=S3 + 불일치 → 상위등급 아님 → gold 아님(편향 방지)
    r = evaluate_consensus("S1", 0.0, "S3", 0.9)
    assert r.is_gold is False and r.status == "disagree"
    assert r.label_source == "llm_judge_uncertain"
    assert r.review_status == "needs_review"


def test_low_conf_agree_not_gold():
    # 동의하지만 신뢰도 부족 + 룰 의견 있음(no_opinion 아님) → low_conf
    r = evaluate_consensus("S2", 0.3, "S2", 0.6)
    assert r.is_gold is False and r.status == "low_conf"
    assert r.label_source == "llm_judge_uncertain"
    assert r.review_status == "needs_review"


def test_disagree_not_gold():
    r = evaluate_consensus("S2", 0.6, "S1", 0.9)
    assert r.is_gold is False and r.status == "disagree"
    assert r.label_source == "llm_judge_uncertain"


def test_threshold_boundaries_inclusive():
    # 경계값(>=)은 충족으로 본다: rule_conf=0.5, llm_conf=0.7 정확히
    r = evaluate_consensus("S1", 0.5, "S1", 0.7)
    assert r.is_gold and r.status == "gold_consensus"
