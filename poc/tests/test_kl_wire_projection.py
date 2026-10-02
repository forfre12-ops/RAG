"""KL(지재원 포털)이 실제로 받는 등급 응답 — kl_wire_projection() 이 좁히는 규칙을 잠근다.

왜 있는가(2026-09-29). 요건: "분류기 등급과 룰분류가 같을 때는 예상 등급 하나만, 다른
경우에는 분류기(label)를 메인으로 하고 룰분류기 예측은 따로, RAG+LLM 인 경우엔 관련 참고도
같이 리턴". `kl_wire_projection()`(schemas/classify.py)이 이 규칙을 구현한다 — label 은
그대로 두고, rule_grade 는 label 과 같으면 지운다(하나만). `regulation_reference_for_kl_wire()`
(services/regulation_evidence_service.py)는 기능이 꺼져 있으면 즉시 None 을 돌려준다(기본값
GPU 없는 배포에서 항상 그렇다).

[2026-10-02 뒤집힘] evaluation_factors·rule_evaluation_factors·evidence·decision_path 는
더 이상 비우지 않는다 — KL포털이 문서마다 매번 사람 검수를 하기로 하면서 "신뢰도는 근거와
같이 보여준다"로 결정이 바뀌었다(9/29 엔 반대로 비웠었다). scores·model_grade·
grade_candidates(_reason)는 **여전히** 비운다 — 이 넷은 이번 결정에서 다시 논의되지 않았다.
같은 날 confidence_tier(계산 필드, confidence 를 3단계로 나눈 검수 우선순위 참고값)와
regulation_summary(선택 옵션, 고른 규정 조항을 로컬 LLM 이 요약한 한두 문장)도 추가됐다.

경계: 이 시험은 두 함수의 단위 동작만 본다. 호출자 역할(kl_backend)에 따라 실제 HTTP 엔드포인트
(GET /classify/jobs/{job_id})·콜백이 이 함수들을 쓰는지는 DB·인증이 필요한 통합 시험
(test_kl_integration.py 류)의 몫이다.
"""
from __future__ import annotations

from uuid import uuid4

import pytest

from koipa.schemas.classify import ClassifyJobResult, compute_confidence_tier, kl_wire_projection
from koipa.schemas.regulation import EvidenceClauseRef, EvidenceItemModel, EvidenceRegulationRef
from koipa.services.regulation_evidence_service import (
    regulation_reference_for_kl_wire,
    regulation_summary_for_kl_wire,
)


def _result(**overrides) -> ClassifyJobResult:
    fields = dict(
        inference_id=uuid4(),
        doc_id="d1",
        label="S2",
        confidence=0.8,
        scores={"S2": 0.8, "S3": 0.2},
        model_version="v-test",
        rule_grade="S2",
        model_grade="S2",
        evaluation_factors=None,
        evidence=[],
        decision_path="agreement",
        grade_candidates=[],
        grade_candidates_reason=None,
        warnings=["some warning"],
        status="staging",
    )
    fields.update(overrides)
    return ClassifyJobResult(**fields)


def test_rule_grade_is_dropped_when_it_agrees_with_label():
    """등급이 같으면 하나만 — rule_grade 는 null 이 된다."""
    r = _result(label="S2", rule_grade="S2")
    projected = kl_wire_projection(r)
    assert projected.label == "S2"
    assert projected.rule_grade is None


def test_rule_grade_is_kept_when_it_differs_from_label():
    """등급이 갈리면 label 이 메인, rule_grade 는 참고로 그대로 남는다."""
    r = _result(label="S2", rule_grade="S1")
    projected = kl_wire_projection(r)
    assert projected.label == "S2"
    assert projected.rule_grade == "S1"


def test_rule_grade_stays_none_when_never_set():
    """룰 판정 자체가 없던 경우(rule_grade=None)는 계속 None — 잘못 채우지 않는다."""
    r = _result(label="S2", rule_grade=None)
    projected = kl_wire_projection(r)
    assert projected.rule_grade is None


def test_some_diagnostic_fields_are_still_always_blanked():
    """scores·model_grade·grade_candidates(_reason) 넷은 그대로 항상 비운다 — 10/2 에도 안 바뀐 넷."""
    r = _result(
        label="S2", rule_grade="S2",
        scores={"S2": 0.8, "S3": 0.2},
        model_grade="S1",
        grade_candidates=["S2", "S3"],
        grade_candidates_reason="접근범위 미확인",
    )
    projected = kl_wire_projection(r)
    assert projected.scores == {}
    assert projected.model_grade is None
    assert projected.grade_candidates == []
    assert projected.grade_candidates_reason is None


def test_evidence_fields_now_survive_projection_2026_10_02():
    """[2026-10-02 뒤집힘] evaluation_factors·rule_evaluation_factors·evidence·decision_path 는
    더 이상 비우지 않는다 — "신뢰도는 근거와 같이 보여준다" 결정."""
    from koipa.schemas.classify import EvaluationFactors, FactorDetail

    factors = EvaluationFactors(
        secrecy=FactorDetail(state="observed", value=2, evidence=["조항 인용"]),
        value=FactorDetail(state="observed", value=2, evidence=["조항 인용"]),
        management=FactorDetail(state="unknown"),
    )
    r = _result(
        label="S2", rule_grade="S2",
        evaluation_factors=factors,
        decision_path="agreement",
    )
    projected = kl_wire_projection(r)
    assert projected.evaluation_factors == factors
    assert projected.decision_path == "agreement"


def test_confidence_tier_is_computed_and_survives_projection():
    """confidence_tier 는 confidence 에서 그대로 파생되고, 투영 뒤에도(confidence 가 안 바뀌므로) 일관된다."""
    r = _result(confidence=0.1)
    assert r.confidence_tier == "낮음"
    assert kl_wire_projection(r).confidence_tier == "낮음"

    r2 = _result(confidence=0.5)
    assert r2.confidence_tier == "보통"

    r3 = _result(confidence=0.9)
    assert r3.confidence_tier == "높음"


@pytest.mark.parametrize(("value", "expected"), [
    (0.0, "낮음"), (0.333, "낮음"), (1 / 3, "보통"), (0.5, "보통"),
    (2 / 3 - 1e-9, "보통"), (2 / 3, "높음"), (1.0, "높음"),
])
def test_compute_confidence_tier_boundaries(value, expected):
    assert compute_confidence_tier(value) == expected


def test_baseline_fields_survive_projection_unchanged():
    """doc_id·inference_id·confidence·status·model_version·warnings 는 그대로 남는다."""
    r = _result(doc_id="doc-123", confidence=0.42, status="needs_review",
                model_version="v-fe4b386b", warnings=["needs_review: low confidence"])
    projected = kl_wire_projection(r)
    assert projected.doc_id == "doc-123"
    assert projected.inference_id == r.inference_id
    assert projected.confidence == 0.42
    assert projected.status == "needs_review"
    assert projected.model_version == "v-fe4b386b"
    assert projected.warnings == ["needs_review: low confidence"]


def test_regulation_reference_is_carried_through_when_given():
    """RAG+LLM 규정참고가 있으면 그대로 실린다."""
    item = EvidenceItemModel(
        regulation=EvidenceRegulationRef(reg_id="r1", name="영업비밀관리규정", version_label="v1"),
        clause=EvidenceClauseRef(clause_id="c1", article_no="제41조", title="설계·공정 문서"),
        sentences=["공정 조건표는 핵심 공정은 극비로 취급한다."],
        is_grade_list=False,
    )
    projected = kl_wire_projection(_result(), regulation_reference=[item])
    assert projected.regulation_reference == [item]


def test_regulation_reference_defaults_to_none():
    """호출한 쪽이 안 건네면(대부분 — 기능 꺼짐·GPU 없음) null."""
    projected = kl_wire_projection(_result())
    assert projected.regulation_reference is None


def test_regulation_summary_is_carried_through_when_given():
    """[2026-10-02] rag_llm 의 요약(regulation_summary)도 있으면 그대로 실린다."""
    projected = kl_wire_projection(_result(), regulation_summary="제41조가 이 문서에 적용됩니다.")
    assert projected.regulation_summary == "제41조가 이 문서에 적용됩니다."


def test_regulation_summary_defaults_to_none():
    projected = kl_wire_projection(_result())
    assert projected.regulation_summary is None


def test_regulation_summary_for_kl_wire_is_none_when_no_reference():
    """항이 없으면(reference=None) LLM 을 부르지 않고 바로 None."""
    assert regulation_summary_for_kl_wire("any-doc-id", None) is None


def test_regulation_summary_for_kl_wire_is_none_when_feature_is_off(monkeypatch):
    """[안전장치] regulation_llm_summary_enabled 가 꺼져 있으면(기본값) 서비스를 부르지도 않고 None."""
    from koipa.config import settings

    item = EvidenceItemModel(
        regulation=EvidenceRegulationRef(reg_id="r1", name="영업비밀관리규정", version_label="v1"),
        clause=EvidenceClauseRef(clause_id="c1", article_no="제41조", title="설계·공정 문서"),
        sentences=["공정 조건표는 핵심 공정은 극비로 취급한다."],
        is_grade_list=False,
    )
    monkeypatch.setattr(settings, "regulation_llm_summary_enabled", False, raising=False)
    assert regulation_summary_for_kl_wire("any-doc-id", [item]) is None


def test_projection_does_not_mutate_the_original():
    """model_copy 라 원본(저장값·내부 응답용)은 그대로다."""
    r = _result(label="S2", rule_grade="S1", scores={"S2": 0.8})
    kl_wire_projection(r)
    assert r.rule_grade == "S1"
    assert r.scores == {"S2": 0.8}


def test_regulation_reference_for_kl_wire_is_none_when_feature_is_off(monkeypatch):
    """[안전장치] 기능(REGULATION_REFERENCE_ENABLED)이 꺼져 있으면(기본값) 서비스를 부르지도 않고 None."""
    from koipa.config import settings

    monkeypatch.setattr(settings, "regulation_reference_enabled", False, raising=False)
    assert regulation_reference_for_kl_wire("any-doc-id") is None


def test_regulation_reference_for_kl_wire_swallows_lookup_errors(monkeypatch):
    """조회 실패(LookupError 등)로 분류 응답 자체가 막히면 안 된다 — None 으로 degrade."""
    from koipa.config import settings
    from koipa.services import regulation_evidence_service as mod

    monkeypatch.setattr(settings, "regulation_reference_enabled", True, raising=False)

    class _Boom:
        def find_for_document(self, doc_id: str):
            raise LookupError("document not found")

    monkeypatch.setattr(mod.RegulationEvidenceService, "get_instance", staticmethod(lambda: _Boom()))
    assert regulation_reference_for_kl_wire("missing-doc") is None


def test_regulation_kl_wire_budget_deadline_skips_single_result(monkeypatch):
    """결과가 1건이면 예산 자체를 안 둔다 — 원래도 그 1건분 지연뿐이다."""
    from koipa.api.async_classify import _regulation_kl_wire_budget_deadline
    from koipa.config import settings

    monkeypatch.setattr(settings, "regulation_llm_select_enabled", True, raising=False)
    assert _regulation_kl_wire_budget_deadline(1) is None
    assert _regulation_kl_wire_budget_deadline(0) is None


def test_regulation_kl_wire_budget_deadline_is_none_when_both_llm_options_off(monkeypatch):
    """[2026-10-02] LLM 옵션이 둘 다 꺼져 있으면(기본값) 예산 자체가 필요 없다 — 호출이 빠르다."""
    from koipa.api.async_classify import _regulation_kl_wire_budget_deadline
    from koipa.config import settings

    monkeypatch.setattr(settings, "regulation_llm_select_enabled", False, raising=False)
    monkeypatch.setattr(settings, "regulation_llm_summary_enabled", False, raising=False)
    assert _regulation_kl_wire_budget_deadline(100) is None


def test_regulation_kl_wire_budget_deadline_caps_many_results(monkeypatch):
    """[2026-10-02 발견] 배치 조회(GET /classify/jobs/{job_id})가 결과 수만큼 LLM 호출을 순차로
    부르다 무한정 늘어지지 않게 전체 예산(2×timeout)을 둔다 — regulation_summary 추가로 문서당
    최대 지연이 거의 2배가 되면서 알아챈 위험."""
    import time

    from koipa.api.async_classify import _regulation_kl_wire_budget_deadline
    from koipa.config import settings

    monkeypatch.setattr(settings, "regulation_llm_select_enabled", True, raising=False)
    monkeypatch.setattr(settings, "regulation_llm_timeout_s", 60.0, raising=False)
    before = time.monotonic()
    deadline = _regulation_kl_wire_budget_deadline(1000)
    assert deadline is not None
    assert 119.0 <= deadline - before <= 121.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
