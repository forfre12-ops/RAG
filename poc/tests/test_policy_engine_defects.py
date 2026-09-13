"""정책 엔진 결함 둘 — 외부 코드 리뷰(2026-09-13)가 재현해 준 것을 잠근다.

① **조건 작성 순서에 따라 검수 여부가 달라졌다.**
   `_condition_results` 가 불충족으로 종료할 때 앞서 쌓인 unknown 을 그대로 반환했다.
   한 조건이라도 '아님' 이 확인되면 그 규칙은 순서와 무관하게 해당 없음이어야 한다.
   종전: 접근범위(미확인) 먼저 → needs_review=True / 공개여부(불충족) 먼저 → False

② **같은 우선순위에서 등급이 갈리면 규칙 id 사전순으로 골랐다.**
   A-low(S2) 가 B-high(TS) 를 이기고 needs_review=False 로 나갔다 — 조용한 하향은 미탐이다.
   FNR-safe 로 더 민감한 등급을 택하고 충돌을 사유에 남긴다.
"""
from __future__ import annotations

from koipa.modules.m3_labeling.policy_engine import Policy, Rule, evaluate

ORDER = ("TS", "S1", "S2", "S3")


def _policy(rules: tuple[Rule, ...]) -> Policy:
    return Policy(org_id="t", version="v1", effective_date="2026-09-13",
                  grade_order=ORDER, default_grade="S3", rules=rules)


def test_condition_order_does_not_change_outcome() -> None:
    """공개됨이 확인돼 상위 규칙이 불충족이면, 미확인 조건이 앞에 있든 뒤에 있든 같아야 한다."""
    facts = {"public_disclosed": True}
    fallback = Rule("R-99", "S3", 90, {"public_disclosed": {"op": "eq", "value": True}}, ())
    results = []
    for when in (
        {"access_scope": {"op": "eq", "value": "approved_only"},
         "public_disclosed": {"op": "eq", "value": False}},
        {"public_disclosed": {"op": "eq", "value": False},
         "access_scope": {"op": "eq", "value": "approved_only"}},
    ):
        p = evaluate(_policy((Rule("R-01", "TS", 10, when, ()), fallback)), facts)
        results.append((p.grade, p.needs_review, tuple(p.blocked_rules)))
    assert results[0] == results[1], f"조건 순서에 따라 결과가 다르다: {results}"
    assert results[0][1] is False, "불충족이 확인된 규칙 때문에 검수로 가면 안 된다"


def test_same_priority_conflict_picks_more_sensitive_and_reviews() -> None:
    """등급이 갈리는 동순위 충돌에서 낮은 등급이 조용히 이기면 안 된다."""
    pol = _policy((
        Rule("A-low", "S2", 50, {"x": {"op": "eq", "value": True}}, ()),
        Rule("B-high", "TS", 50, {"x": {"op": "eq", "value": True}}, ()),
    ))
    p = evaluate(pol, {"x": True})
    assert p.grade == "TS", f"더 민감한 등급을 택해야 한다 — got {p.grade}"
    assert p.needs_review is True, "충돌은 검수로 보내야 한다"
    assert "A-low" in p.reason and "B-high" in p.reason, "충돌한 규칙을 사유에 남겨야 한다"


def test_same_priority_same_grade_is_not_a_conflict() -> None:
    """등급이 같으면 충돌이 아니다 — 불필요한 검수를 만들지 않는다."""
    pol = _policy((
        Rule("A", "S2", 50, {"x": {"op": "eq", "value": True}}, ()),
        Rule("B", "S2", 50, {"x": {"op": "eq", "value": True}}, ()),
    ))
    p = evaluate(pol, {"x": True})
    assert p.grade == "S2"
    assert p.needs_review is False, "같은 등급이면 검수로 보낼 이유가 없다"


def test_missing_evidence_still_blocks_and_reviews() -> None:
    """①을 고치면서 '증거 부족은 보류' 라는 본래 불변식이 깨지지 않아야 한다."""
    pol = _policy((
        Rule("R-01", "TS", 10, {"access_scope": {"op": "eq", "value": "approved_only"}},
             ("access_scope",)),
        Rule("R-50", "S2", 50, {"x": {"op": "eq", "value": True}}, ()),
    ))
    p = evaluate(pol, {"x": True})
    assert "R-01" in p.blocked_rules, "증거 없는 상위 규칙은 보류돼야 한다"
    assert p.needs_review is True, "상위 규칙이 보류되면 검수로 가야 한다"
