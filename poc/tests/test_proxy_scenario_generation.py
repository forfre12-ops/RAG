"""Catalog-driven generation must carry the scenario into the LLM prompt."""

from __future__ import annotations

import json
from types import SimpleNamespace

from koipa.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator


class _FakeProvider:
    name = "fake"
    model = "fake-model"

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def generate(self, prompt: str, *, system: str, **_: object) -> SimpleNamespace:
        self.calls.append((prompt, system))
        return SimpleNamespace(
            text=json.dumps(
                {
                    "title": "Scenario report",
                    "body": "x" * 1300,
                    "document_type": "report",
                    "dept_hint": "R&D",
                    "rationale_tags": ["contextual"],
                }
            ),
            usage=None,
        )


def test_catalog_scenario_overrides_generic_generation_context():
    provider = _FakeProvider()
    doc = SyntheticDocGenerator(llm=provider).generate_one(
        SynthRequest(
            target_grade="TS",
            domain="tech",
            scenario_context="CUSTOM_SCENARIO_CONTEXT",
            disclosure_scope="CUSTOM_DISCLOSURE_SCOPE",
            harm_potential="CUSTOM_HARM_POTENTIAL",
            document_type_hint="CUSTOM_DOCUMENT_TYPE",
            len_min=1200,
            len_max=2200,
            structure_requirements="CUSTOM_STRUCTURE_REQUIREMENTS",
            revision_context="CUSTOM_REVISION_CONTEXT",
        )
    )

    prompt, system = provider.calls[0]
    assert "CUSTOM_SCENARIO_CONTEXT" in prompt
    assert "CUSTOM_DISCLOSURE_SCOPE" in prompt
    assert "CUSTOM_HARM_POTENTIAL" in prompt
    assert "CUSTOM_DOCUMENT_TYPE" in prompt
    assert "CUSTOM_STRUCTURE_REQUIREMENTS" in prompt
    assert "CUSTOM_REVISION_CONTEXT" in prompt
    assert "사실 원장 우선순위" in prompt
    assert "원장 밖의 기초 수치나 파생 수치를 임의로 보충하지" in prompt
    assert "파생값은 기초값으로 다시 계산" in system
    assert "정량 사실 출력 금지 또는 숫자 금지" in system
    assert "검산이 확실하지 않은 파생값은" in system
    assert "사람 이름은 가명도 만들지 말고" in system
    assert "하나의 기준연도" in system
    assert "정상범위·경고값·실패경계" in system
    assert "정량 출력이 허용된 경우에만 정상범위·실패경계" in prompt
    assert doc.document_type == "report"


def test_generic_request_keeps_optional_proxy_controls_optional():
    provider = _FakeProvider()
    SyntheticDocGenerator(llm=provider).generate_one(
        SynthRequest(target_grade="S2", domain="business")
    )

    prompt, _ = provider.calls[0]
    assert "문서 유형에 자연스러운 여러 절과 항목" in prompt
    assert "[재작성 참고]" not in prompt
