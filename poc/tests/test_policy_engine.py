"""고객사 정책 엔진 — 사실 → 그 고객사 등급 **후보**.

왜 이 시험이 있는가(2026-09-13). 고객사마다 등급의 뜻이 다르다. 같은 문서가 A사에서는
"1급 비밀", B사에서는 "경영기밀"이다. 그래서 등급을 공통 골든셋으로 만들면 고객사 데이터가
섞이는 순간 정답이 충돌한다. 층을 갈라서 — 모델은 **사실**, 정책 엔진은 **고객사 등급** —
푸는 구조이고, 이 시험은 그 엔진이 지켜야 할 것 넷을 지킨다.

그중 하나가 결정적이다: **증거가 없으면 하향하지 않는다.** 빠진 증거를 '조건 미충족'으로
읽으면 더 낮은 규칙이 이겨서 하향이 되고, 그것이 곧 미탐이다. 실측 근거도 있다 —
관리성 서술을 지우니 정확도는 1.1%p 만 떨어졌는데 **미탐이 1건 → 21건(23배)** 이었다
(2026-09-13 · [[comparison-eval-set-shortcut-controlled-2026-09-12]]).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from koipa.modules.m3_labeling.policy_engine import (
    Policy,
    Rule,
    evaluate,
    load,
    validate,
)

_POC = Path(__file__).resolve().parents[1]
TEMPLATE = _POC / "datasets" / "mapping_tables" / "POLICY_TEMPLATE.json"


def _policy(**over) -> Policy:
    base = dict(
        org_id="member-0142", version="v3", effective_date="2026-01-01",
        grade_order=("1급 비밀", "2급 비밀", "대외비", "일반"),
        default_grade="일반",
        rules=(
            Rule(id="A-17", grade="1급 비밀", priority=10,
                 when={"public_disclosed": {"op": "eq", "value": False},
                       "access_scope": {"op": "in", "value": ["approved_only", "designated"]}},
                 requires_evidence=("access_scope",)),
            Rule(id="A-40", grade="대외비", priority=50,
                 when={"public_disclosed": {"op": "eq", "value": False}}),
            Rule(id="A-90", grade="일반", priority=90,
                 when={"public_disclosed": {"op": "eq", "value": True}}),
        ),
    )
    base.update(over)
    return Policy(**base)


def test_template_passes_validation_as_shipped():
    """양식 그대로도 검사를 통과해야 한다 — 통과 못 하면 담당자가 첫 줄에서 막힌다."""
    assert validate(load(TEMPLATE)) == []


def test_template_proposes_the_documented_example():
    """양식에 적은 예시(A-17: 미공개+가격+제한열람 → 1급 비밀)가 실제로 그렇게 나오는가."""
    policy = load(TEMPLATE)
    out = evaluate(policy, {
        "public_disclosed": False, "content_kinds": ["가격", "원가"],
        "access_scope": "approved_only",
    })
    assert out.grade == "1급 비밀"
    assert out.rule_id == "A-17"
    assert out.needs_review is False


def test_missing_evidence_does_not_downgrade():
    """⭐ 증거가 없으면 더 낮은 규칙이 이기게 두지 않는다 — 그것이 곧 미탐이다."""
    out = evaluate(_policy(), {"public_disclosed": False})  # access_scope 없음
    assert "access_scope" in out.missing_evidence
    assert "A-17" in out.blocked_rules
    assert out.needs_review is True, "더 높은 등급을 판단 못 했으면 검수로 보내야 한다"


def test_missing_evidence_keeps_the_lower_candidate_but_flags_it():
    """하향은 막되, 판단된 후보 자체는 낸다 — 아무것도 안 내면 화면이 비어 버린다."""
    out = evaluate(_policy(), {"public_disclosed": False})
    assert out.grade == "대외비" and out.rule_id == "A-40"
    assert out.needs_review is True


def test_lower_blocked_rule_does_not_force_review():
    """보류된 규칙이 **더 낮은** 등급이면 검수로 보낼 이유가 없다 — 과잉 검수를 만들지 않는다."""
    policy = _policy(rules=(
        Rule(id="hi", grade="1급 비밀", priority=10,
             when={"public_disclosed": {"op": "eq", "value": False}}),
        Rule(id="lo", grade="대외비", priority=50,
             when={"public_disclosed": {"op": "eq", "value": False}},
             requires_evidence=("dlp_label",)),
    ))
    out = evaluate(policy, {"public_disclosed": False})
    assert out.grade == "1급 비밀"
    assert "lo" in out.blocked_rules
    assert out.needs_review is False


def test_priority_decides_between_matching_rules():
    out = evaluate(_policy(), {"public_disclosed": False, "access_scope": "designated"})
    assert out.rule_id == "A-17", "priority 가 낮은 규칙이 이겨야 한다"


def test_result_carries_policy_version_and_date():
    """나중에 '그때 무슨 규칙으로 그랬나'에 답해야 한다."""
    out = evaluate(_policy(), {"public_disclosed": True})
    assert out.policy_version == "v3" and out.effective_date == "2026-01-01"
    assert out.rule_id == "A-90"


def test_same_facts_same_result():
    """재현 가능해야 한다 — 판정 경로에 확률적 요소가 없다."""
    facts = {"public_disclosed": False, "access_scope": "designated"}
    first = evaluate(_policy(), facts).to_dict()
    for _ in range(5):
        assert evaluate(_policy(), facts).to_dict() == first


def test_no_rule_matches_falls_back_to_default():
    out = evaluate(_policy(), {"public_disclosed": None})
    assert out.grade == "일반" and out.rule_id is None


@pytest.mark.parametrize("broken,expect", [
    ({"grade_order": ()}, "grade_order"),
    ({"version": ""}, "policy_version"),
    ({"effective_date": ""}, "effective_date"),
])
def test_validation_catches_missing_contract_fields(broken, expect):
    issues = validate(_policy(**broken))
    assert any(expect in i for i in issues), issues


def test_validation_catches_unknown_fact_name():
    """오타 난 사실 이름은 그 조건을 **조용히 무시**시킨다 — 잡아야 한다."""
    policy = _policy(rules=(
        Rule(id="x", grade="대외비", when={"acess_scope": {"op": "exists"}}),
    ))
    assert any("acess_scope" in i for i in validate(policy))


def test_validation_catches_unknown_operator():
    policy = _policy(rules=(
        Rule(id="x", grade="대외비", when={"access_scope": {"op": "similar_to", "value": "x"}}),
    ))
    assert any("similar_to" in i for i in validate(policy))


def test_validation_catches_grade_outside_order():
    policy = _policy(rules=(Rule(id="x", grade="극비", when={"public_disclosed": {"op": "exists"}}),))
    assert any("극비" in i for i in validate(policy))


def test_underscore_keys_are_guidance_not_rules(tmp_path):
    """양식 안의 안내문이 규칙으로 읽히면 안 된다."""
    path = tmp_path / "p.json"
    path.write_text(json.dumps({
        "org_id": "x", "policy_version": "v1", "effective_date": "2026-01-01",
        "grade_order": ["갑", "을"], "default_grade": "을",
        "rules": [{"id": "_설명", "grade": "갑"},
                  {"id": "R1", "grade": "갑", "when": {"public_disclosed": {"op": "exists"}}}],
    }, ensure_ascii=False), encoding="utf-8")
    policy = load(path)
    assert [r.id for r in policy.rules] == ["R1"]
    assert validate(policy) == []
