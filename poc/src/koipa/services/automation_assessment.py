"""자동확정 정책 학습을 위한 판단 근거 스냅샷.

``confidence``는 모델이 선택한 등급의 점수일 뿐, 사람이 확인 없이 확정해도 될
확률과 동의어가 아니다. 이 모듈은 현재 정책을 바꾸지 않고, 이후 사람 확정/재라벨
결과와 결합해 자동확정 위험도를 보정할 수 있는 비민감 신호만 동결한다.

숫자 하나를 임의로 합산해 "더 높은 confidence"처럼 보이게 하지 않는다. 보정된
자동확정 점수는 충분한 독립 검증 라벨로 적합한 뒤에만 별도 정책 버전으로 추가한다.
"""

from __future__ import annotations

from typing import Any

from koipa.schemas.classify import AutomationAssessment
from koipa.services.review_reasons import causal_review_reason, gate_hits


ASSESSMENT_SCHEMA_VERSION = "auto-confirm-assessment-v1"
SHADOW_MODE = "collect_only"


def _code(value: Any) -> str | None:
    if value is None:
        return None
    return value.value if hasattr(value, "value") else str(value)


def build_automation_assessment(
    prediction: Any,
    *,
    status: str,
    warnings: list[str] | None = None,
) -> AutomationAssessment:
    """현재 분류 결정의 자동확정 학습용 특징을 동결한다.

    반환값은 관측용(shadow)이며, ``status`` 또는 기존 게이트를 변경하지 않는다.
    ``scores``는 모델/후처리 뒤 최종 등급 분포를 그대로 사용해, 재현 시 다른 모델을
    다시 실행할 필요가 없도록 한다.
    """
    scores = {
        str(code): float(score)
        for code, score in (getattr(prediction, "scores", None) or {}).items()
    }
    ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
    selected_label = _code(getattr(prediction, "label", None)) or ""
    selected_rank = next(
        (index + 1 for index, (code, _) in enumerate(ranked) if code == selected_label),
        None,
    )
    top_label, top_score = (ranked[0] if ranked else (None, None))
    runner_up_label, runner_up_score = (
        ranked[1] if len(ranked) > 1 else (None, None)
    )
    margin = (
        float(top_score - runner_up_score)
        if top_score is not None and runner_up_score is not None
        else None
    )
    rule_grade = _code(getattr(prediction, "rule_grade", None))
    review_hits = gate_hits(warnings)

    return AutomationAssessment(
        schema_version=ASSESSMENT_SCHEMA_VERSION,
        shadow_mode=SHADOW_MODE,
        selected_label=selected_label,
        selected_confidence=float(getattr(prediction, "confidence", 0.0)),
        selected_rank=selected_rank,
        top_label=top_label,
        top_score=top_score,
        runner_up_label=runner_up_label,
        runner_up_score=runner_up_score,
        score_margin=margin,
        rule_grade=rule_grade,
        model_grade=_code(getattr(prediction, "model_grade", None)),
        rule_agrees=(rule_grade == selected_label) if rule_grade else None,
        rule_has_evidence=getattr(prediction, "rule_has_evidence", None),
        evidence_count=len(getattr(prediction, "evidence", None) or []),
        rag_context_count=len(getattr(prediction, "rag_context", None) or []),
        current_policy_status=status,
        current_policy_eligible=(status == "staging"),
        causal_review_reason=causal_review_reason(warnings, status),
        review_gate_hits=review_hits,
    )
