from types import SimpleNamespace

import pytest

from koipa.services.automation_assessment import build_automation_assessment


def _prediction(**overrides):
    base = {
        "label": "TS",
        "confidence": 0.31,
        "scores": {"TS": 0.31, "S1": 0.04, "S2": 0.10, "S3": 0.55},
        "rule_grade": "S2",
        "model_grade": "S2",
        "rule_has_evidence": True,
        "evidence": [object()],
        "rag_context": [],
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_assessment_keeps_selected_score_distinct_from_top_score():
    assessment = build_automation_assessment(_prediction(), status="needs_review", warnings=[
        "low-confidence: confidence=0.31 < 0.70 — review recommended",
    ])

    assert assessment.selected_label == "TS"
    assert assessment.selected_confidence == 0.31
    assert assessment.selected_rank == 2
    assert assessment.top_label == "S3"
    assert assessment.top_score == 0.55
    assert assessment.score_margin == pytest.approx(0.24)
    assert assessment.causal_review_reason == "low-confidence"
    assert assessment.current_policy_eligible is False


def test_assessment_records_all_gate_hits_but_one_causal_reason():
    assessment = build_automation_assessment(_prediction(), status="needs_review", warnings=[
        "low-confidence: confidence=0.31 < 0.70 — review recommended",
        "agreement-gate: model=TS vs rule=S2 disagree on non-public grade",
    ])

    assert assessment.review_gate_hits == ["low-confidence", "agreement-gate"]
    assert assessment.causal_review_reason == "low-confidence"


def test_assessment_never_assigns_unvalidated_reliability_score():
    assessment = build_automation_assessment(_prediction(), status="staging", warnings=[])

    assert assessment.shadow_mode == "collect_only"
    assert assessment.current_policy_eligible is True
    assert assessment.causal_review_reason is None
