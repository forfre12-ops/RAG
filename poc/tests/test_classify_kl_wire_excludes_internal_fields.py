"""KL 이 받는 분류 결과(IF-05 결과 · 콜백 본문)에 내부 전용 필드가 안 실리는지 잠근다.

왜 있는가(2026-09-27). "최대한 불필요한 건 API 에서 빼자"는 방침에 따라 `automation_assessment`
(자동확정 정책 검증용 그림자 관측치, 규약서·코드 스스로 "연동에 쓰지 않는다"고 적어 둔 값)를
`ClassifyOutcome`(IF-05·콜백 공용 기반)에서 `ClassifyResponse`(내부 전용 동기 응답)로 옮겼다.
이 시험이 없으면 다음 사람이 `ClassifyOutcome`에 새 내부 전용 필드를 아무 생각 없이 얹어
같은 문제를 반복하기 쉽다 — `automation_assessment` 자체가 그렇게 생겼었다.

경계: 이 시험은 KL 이 실제로 받는 값(IF-05 results[] · 콜백 본문 · GET /classify/{doc_id})에
없어야 할 것을 확인한다. DB 저장(`_try_persist`)이 값을 계속 받는지는
`tests/test_automation_assessment.py`·`tests/test_classify_needs_review_gate.py` 가 다룬다.
"""
from __future__ import annotations

from uuid import uuid4

from koipa.schemas.classify import (
    AutomationAssessment,
    ClassifyJobResult,
    ClassifyOutcome,
    ClassifyResponse,
    StoredClassificationResponse,
)


def _assessment() -> AutomationAssessment:
    return AutomationAssessment(
        schema_version="auto-confirm-assessment-v1",
        selected_label="S2",
        selected_confidence=0.8,
        current_policy_status="needs_review",
        current_policy_eligible=False,
    )


def _response(**overrides) -> ClassifyResponse:
    fields = dict(
        inference_id=uuid4(),
        doc_id="d1",
        label="S2",
        confidence=0.8,
        scores={"S2": 0.8},
        model_version="v-test",
        elapsed_ms=10,
        automation_assessment=_assessment(),
    )
    fields.update(overrides)
    return ClassifyResponse(**fields)


def test_automation_assessment_is_not_declared_on_the_shared_outcome_base():
    """ClassifyOutcome 은 ClassifyResponse·ClassifyJobResult 가 함께 쓰는 기반이다 — 여기 두면 둘 다에 실린다."""
    assert "automation_assessment" not in ClassifyOutcome.model_fields


def test_automation_assessment_stays_on_the_internal_sync_response():
    """POST /classify·/classify/explain(둘 다 x-audience: internal) 은 여전히 값을 받는다 — 리뷰 화면용."""
    assert "automation_assessment" in ClassifyResponse.model_fields
    r = _response()
    assert r.automation_assessment is not None
    assert r.automation_assessment.selected_label == "S2"


def test_automation_assessment_is_absent_from_the_kl_facing_job_result_schema():
    """IF-05 results[]/콜백 본문이 실제로 쓰는 타입에는 필드 자체가 없다(null 로 감추는 것이 아니라 없다)."""
    assert "automation_assessment" not in ClassifyJobResult.model_fields
    assert "automation_assessment" not in StoredClassificationResponse.model_fields


def test_job_result_dict_omits_automation_assessment_even_when_set():
    """job_result() 는 IF-05 results[]·콜백 본문으로 그대로 나가는 JSON 을 만든다(services/async_classify_service.py·workers/tasks.py)."""
    d = _response().job_result()
    assert "automation_assessment" not in d
    assert "elapsed_ms" not in d
    # 대조군 — 그 밖의 선택 항목은 그대로 실린다(자동확정 관측치만 빠진 것이지, 전부 걷어낸 게 아님).
    assert "evaluation_factors" in d and "grade_candidates" in d


def test_job_result_round_trips_into_classify_job_result_without_the_field():
    """실제 파이프라인 순서 재현: job_result() 로 만든 dict 를 ClassifyJobResult 로 재검증(async_classify_service.get_status)."""
    d = _response(automation_assessment=_assessment()).job_result()
    jr = ClassifyJobResult.model_validate(d)
    assert not hasattr(jr, "automation_assessment")
    assert jr.label == "S2" and jr.doc_id == "d1"


def test_mutation_reintroducing_the_field_on_outcome_would_leak_it_to_kl():
    """대조군 — automation_assessment 를 ClassifyOutcome 에 다시 얹으면 이 시험들이 전부 잡아야 한다.

    실제로 다시 얹지는 않는다(대상 코드를 건드리면 다른 시험이 깨진다) — 여기서는 위 네 시험이
    '있다/없다'를 각기 다른 각도(선언·상속·직렬화·재검증)에서 본다는 것만 표로 남긴다.
    """
    assert {
        "declared_on_outcome": "automation_assessment" in ClassifyOutcome.model_fields,
        "declared_on_job_result": "automation_assessment" in ClassifyJobResult.model_fields,
        "in_job_result_dict": "automation_assessment" in _response().job_result(),
    } == {
        "declared_on_outcome": False,
        "declared_on_job_result": False,
        "in_job_result_dict": False,
    }
