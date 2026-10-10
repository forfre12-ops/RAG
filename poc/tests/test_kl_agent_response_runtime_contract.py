"""배포 E2E가 검사하는 KL Agent 최종 결과 계약의 순수 단위 시험.

실서버 호출은 ``scripts/e2e_kl_api_flow.py``가 담당한다. 이 시험은 그 도구가
RAG/LLM 보조 기능이 비어 있을 때도 대표 답을 요구하는지 빠르게 잠근다.
"""

from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.e2e_kl_api_flow import kl_agent_result_contract_problems


def _result(**overrides: object) -> dict:
    result: dict = {
        "label": "S2",
        "rule_grade": "S1",
        "status": "needs_review",
        "warnings": ["needs_review: agreement"],
        "confidence": 0.72,
        "confidence_tier": "높음",
        "evidence": [],
        "decision_path": "rule-override",
        "regulation_reference": None,
        "regulation_summary": None,
        "explanation_status": "unavailable",
        "fallback_reason": "regulation_reference_unavailable",
    }
    result.update(overrides)
    return result


def test_unavailable_explanation_still_has_a_valid_agent_answer():
    assert kl_agent_result_contract_problems(_result()) == []


def test_rule_agreement_must_not_be_sent_as_a_second_grade():
    problems = kl_agent_result_contract_problems(_result(rule_grade="S2"))
    assert "rule_grade must be null when it agrees with label" in problems


def test_fallback_without_a_label_is_not_an_acceptable_response():
    problems = kl_agent_result_contract_problems(_result(label=None))
    assert any(problem.startswith("label must be") for problem in problems)


def test_available_explanation_requires_source_and_summary():
    problems = kl_agent_result_contract_problems(
        _result(explanation_status="available", fallback_reason=None)
    )
    assert "available requires regulation_reference" in problems
    assert "available requires regulation_summary" in problems


def test_reference_only_is_valid_without_a_summary():
    result = _result(
        explanation_status="reference_only",
        fallback_reason=None,
        regulation_reference=[{"clause": "제41조"}],
    )
    assert kl_agent_result_contract_problems(result) == []
