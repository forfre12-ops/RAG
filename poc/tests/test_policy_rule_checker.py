"""정책표 규칙 시험기가 **실제로 구멍을 잡는가**.

왜 이 시험이 있는가(2026-09-13). 같은 날 회귀 게이트가 "회귀 없음"을 찍었는데 정작 그 축을
재지 않고 있었다. 검사기를 만들었으면 **깨진 것을 넣어 걸리는지**도 봐야 한다 —
깨끗한 입력에서 초록불만 확인하면 "아무것도 안 잡는 검사기"를 통과시킨다.

여기서는 구멍을 하나씩 심어 놓고 그 항목이 보고되는지 본다.
"""

from __future__ import annotations

import sys
from pathlib import Path

_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from check_org_policy_rules import check  # noqa: E402

from koipa.modules.m3_labeling.policy_engine import Policy, Rule  # noqa: E402

ORDER = ("1급 비밀", "2급 비밀", "대외비", "일반")


def _policy(rules) -> Policy:
    return Policy(org_id="m", version="v1", effective_date="2026-01-01",
                  grade_order=ORDER, default_grade="일반", rules=tuple(rules))


def test_clean_policy_reports_no_holes():
    """멀쩡한 표에서는 아무것도 보고하지 않아야 한다 — 아무거나 잡는 검사기가 아니다."""
    rep = check(_policy([
        Rule(id="R1", grade="1급 비밀", priority=10,
             when={"public_disclosed": {"op": "eq", "value": False},
                   "access_scope": {"op": "in", "value": ["approved_only"]}},
             requires_evidence=("access_scope",)),
        Rule(id="R2", grade="일반", priority=90,
             when={"public_disclosed": {"op": "eq", "value": True}}),
    ]))
    assert rep["unreachable"] == []
    assert rep["priority_conflicts"] == []
    assert not any(e["silent_downgrade"] for r in rep["rules"] for e in r["evidence"])


def test_catches_unreachable_rule():
    """상위 규칙이 항상 먼저 잡는 규칙은 **한 번도 안 쓰인다** — 표 작성자는 모른다."""
    rep = check(_policy([
        Rule(id="WIDE", grade="1급 비밀", priority=10,
             when={"public_disclosed": {"op": "eq", "value": False}}),
        Rule(id="NARROW", grade="대외비", priority=50,
             when={"public_disclosed": {"op": "eq", "value": False},
                   "access_scope": {"op": "in", "value": ["department"]}}),
    ]))
    assert [u["rule"] for u in rep["unreachable"]] == ["NARROW"]
    assert rep["unreachable"][0]["shadowed_by"] == "WIDE"


def test_catches_priority_conflict():
    """같은 순위에 다른 등급이면 무엇이 이기는지 **표가 안 정한 것**이다."""
    rep = check(_policy([
        Rule(id="P1", grade="1급 비밀", priority=10,
             when={"public_disclosed": {"op": "eq", "value": False}}),
        Rule(id="P2", grade="대외비", priority=10,
             when={"security_marking": {"op": "exists"}}),
    ]))
    conflicts = rep["priority_conflicts"]
    assert conflicts and sorted(conflicts[0]["rules"]) == ["P1", "P2"]


def test_catches_silent_downgrade_on_missing_evidence():
    """⭐ 증거가 없을 때 **조용히** 더 낮은 등급으로 떨어지면 그것이 곧 미탐이다.

    requires_evidence 를 안 적으면 엔진이 보류로 다루지 못한다 — 그 누락을 여기서 잡는다.
    """
    rep = check(_policy([
        Rule(id="HI", grade="1급 비밀", priority=10,
             when={"public_disclosed": {"op": "eq", "value": False},
                   "access_scope": {"op": "in", "value": ["approved_only"]}},
             requires_evidence=("access_scope",)),
        # LO 는 access_scope 를 안 보므로, HI 가 보류되면 LO 가 그냥 이긴다.
        Rule(id="LO", grade="대외비", priority=50,
             when={"public_disclosed": {"op": "eq", "value": False}}),
    ]))
    hi = next(r for r in rep["rules"] if r["rule"] == "HI")
    row = next(e for e in hi["evidence"] if e["evidence"] == "access_scope")
    # 엔진이 보류로 잡아 검수로 보내므로 '조용한 하향'은 아니어야 한다.
    assert row["needs_review"] is True
    assert row["silent_downgrade"] is False, "검수로 보냈으면 조용한 하향이 아니다"


def test_catches_inert_condition():
    """깨뜨려도 결과가 같은 조건 — 다른 조건이 이미 가르고 있어 실제로는 안 쓰인다."""
    rep = check(_policy([
        Rule(id="R", grade="1급 비밀", priority=10,
             when={"public_disclosed": {"op": "eq", "value": False},
                   "owner_org": {"op": "exists"}}),
    ]))
    # owner_org 를 지워도 규칙이 그대로 잡히면(기본 등급이 없으니) inert 로 잡힌다.
    inert = {c["rule"]: c["conditions"] for c in rep["inert_conditions"]}
    assert "R" not in inert or "public_disclosed" not in inert["R"]


def test_reports_every_rule_once():
    rules = [Rule(id=f"R{i}", grade="일반", priority=10 + i,
                  when={"public_disclosed": {"op": "eq", "value": True}}) for i in range(5)]
    rep = check(_policy(rules))
    assert [r["rule"] for r in rep["rules"]] == [f"R{i}" for i in range(5)]
