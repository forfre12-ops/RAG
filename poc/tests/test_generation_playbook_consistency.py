# -*- coding: utf-8 -*-
"""생성규칙(generation_playbook)이 시스템 프롬프트와 어긋나지 않는가 — 2026-09-29.

규칙 6 이 "회사·사람·제품 이름은 가상이되 자연스럽게"라고 해서, 시스템 프롬프트의 "사람 이름은 가명도
만들지 말고 역할 식별자만"과 정면으로 어긋나 있었다. 개인정보 방침은 시스템 프롬프트가 정본이다.
두 문구가 다시 갈라지면 여기서 걸린다.
"""

from __future__ import annotations

from koipa.modules.m1_synthesis.generation_playbook import GENERATION_RULES
from koipa.modules.m1_synthesis.generator import SYSTEM_PROMPT


def test_system_prompt_is_still_the_person_name_authority():
    """이 시험의 전제 — 시스템 프롬프트가 사람 이름을 역할 식별자로만 쓰게 한다."""
    assert "가명도 만들지 말고" in SYSTEM_PROMPT
    assert "역할 식별자만" in SYSTEM_PROMPT


def test_playbook_does_not_tell_the_model_to_invent_person_names():
    rule = next(r for r in GENERATION_RULES if "별칭 코드" in r)

    assert "사람·제품" not in rule and "사람 이름은 가상" not in rule, (
        "규칙 6 이 사람 이름을 가상으로 지어 쓰라고 한다 — 시스템 프롬프트의 개인정보 방침과 충돌한다"
    )
    assert "역할로만" in rule


def test_playbook_does_not_copy_the_system_prompt_example_token():
    """시스템 프롬프트의 예시 낱말을 규칙에 옮겨 적으면 도메인과 무관하게 그 낱말이 퍼진다.

    첫 수정안이 "[공정책임자A]·품질관리팀처럼"을 적었더니 로컬 qwen3:14b 16건 중 그 낱말이 든 문서가
    3→9건으로 늘었고 hr·public·기타 문서에도 들어갔다(scripts/ab_synth_playbook_variants.py B 팔).
    """
    from koipa.modules.m1_synthesis.generation_playbook import playbook_text

    assert "공정책임자" not in playbook_text()


# ── 프롬프트 조립 — 기본 구조 문구와 규칙 2 가 부딪히지 않는가 ─────────────────────
# 9/28 에는 기본 구조 문구("여러 절과 항목을 사용하고…") 뒤에 규칙을 덧붙였다. 그 문구가 규칙 2("모든
# 문서를 보고서 구조로 쓰지 않는다")와 정면으로 부딪혀 규칙 2 가 사실상 듣지 않았다(로컬 qwen3:14b 16건 A/B —
# 보고서식 구조가 든 문서 덧붙이기 9~12건, 대체 1~2건). 규칙이 켜져 있고 호출자가 구조 요구를 안 줬으면
# 기본 문구를 넣지 않는다.

def _user_prompt(*, use_playbook: bool, structure: str = "", override: str = "") -> str:
    from koipa.adapters.llm import NoopProvider
    from koipa.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator

    gen = SyntheticDocGenerator(llm=NoopProvider(), use_playbook=use_playbook)
    req = SynthRequest(target_grade="S1", domain="tech", structure_requirements=structure)
    return gen._build_user_prompt(req, "S1", "tech", structure_override=override)


def test_default_structure_sentence_is_not_added_when_playbook_is_on():
    from koipa.modules.m1_synthesis.generation_playbook import playbook_text
    from koipa.modules.m1_synthesis.generator import _DEFAULT_STRUCTURE_REQUIREMENTS

    prompt = _user_prompt(use_playbook=True)

    assert playbook_text() in prompt
    assert _DEFAULT_STRUCTURE_REQUIREMENTS not in prompt, (
        "기본 구조 문구가 규칙 2 와 부딪힌다 — 규칙이 켜져 있으면 넣지 않는다"
    )


def test_default_structure_sentence_stays_when_playbook_is_off():
    from koipa.modules.m1_synthesis.generation_playbook import playbook_text
    from koipa.modules.m1_synthesis.generator import _DEFAULT_STRUCTURE_REQUIREMENTS

    prompt = _user_prompt(use_playbook=False)

    assert _DEFAULT_STRUCTURE_REQUIREMENTS in prompt
    assert playbook_text() not in prompt


def test_explicit_structure_requirement_still_gets_the_playbook_appended():
    """카탈로그 러너처럼 구조 요구를 직접 주는 호출은 종전과 같다 — 그 뒤에 규칙이 붙는다."""
    from koipa.modules.m1_synthesis.generation_playbook import playbook_text

    prompt = _user_prompt(use_playbook=True, structure="첫째 절은 결재란, 둘째 절은 사유를 쓴다.")

    assert "첫째 절은 결재란" in prompt
    assert prompt.index("첫째 절은 결재란") < prompt.index(playbook_text())


def test_multi_step_outline_override_still_gets_the_playbook_appended():
    from koipa.modules.m1_synthesis.generation_playbook import playbook_text

    prompt = _user_prompt(use_playbook=True, override="개요: 배경 → 시험 결과 → 조치")

    assert "개요: 배경 → 시험 결과 → 조치" in prompt
    assert playbook_text() in prompt
