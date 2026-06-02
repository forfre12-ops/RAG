"""M5 Inference — rule-fallback 경로 (모델 가중치 없을 때)."""

from __future__ import annotations

import pytest

from lloydk.modules.m5_inference import InferencePipeline

pytestmark = pytest.mark.slow


def test_rule_fallback_predicts_all_grades():
    pipe = InferencePipeline()
    cases = [
        ("TS", "특급기밀 핵심 원천기술 M&A 계획 차세대 제품 설계도"),
        ("S1", "1급 비밀 영업비밀 공정 노하우 고객 데이터베이스 마케팅 전략"),
        ("S2", "대외비 내부 검토 분기 매출 사업 계획 거래처 명단"),
        ("S3", "보도자료 공시 채용 공고 회사 소개 이용약관"),
    ]
    for truth, text in cases:
        res = pipe.run(text=text, use_rag=False, metadata={}, return_evidence=True)
        pred = res.label.value if hasattr(res.label, "value") else str(res.label)
        assert pred == truth, f"target={truth}, predicted={pred}"
        assert res.model_version == "rule-fallback-v0"
        assert sum(res.scores.values()) > 0.99  # 확률 합 ≈ 1


def test_evidence_returned_when_requested():
    pipe = InferencePipeline()
    res = pipe.run(text="특급기밀 차세대 제품 설계도", return_evidence=True)
    assert len(res.evidence) > 0


def test_evidence_omitted_when_not_requested():
    pipe = InferencePipeline()
    res = pipe.run(text="특급기밀 차세대 제품 설계도", return_evidence=False)
    assert res.evidence == []
