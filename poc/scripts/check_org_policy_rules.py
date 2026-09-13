#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""회원사 정책표를 **기계적으로 시험한다** — 규칙마다 사례를 만들어 돌려 본다.

■ 무엇을 찾는가

정책표는 사람이 손으로 쓴다. 규칙이 스무 개만 넘어가면 **쓴 사람도 모르는 구멍**이 생긴다.
고객사 문서가 한 건도 없어도 이 구멍은 찾을 수 있다 — 규칙 자체를 시험하면 되기 때문이다.

    도달 불가 규칙   조건은 맞는데 **항상 상위 규칙에 가려져** 한 번도 못 쓰인다
    증거 미비 처리   필요한 증거가 없을 때 **조용히 하향**되지는 않는가
    우선순위 충돌    같은 priority 에 서로 다른 등급 — 무엇이 이기는지 표가 안 정한다
    조건 무력        어느 조건을 깨뜨려도 결과가 같다 = 그 조건이 실제로는 안 쓰인다

■ 왜 이것이 문서보다 먼저인가

고객사 등급은 이 표가 정한다. 표가 틀리면 문서를 아무리 잘 읽어도 등급이 틀린다.
그리고 표의 오류는 **문서를 받기 전에** 고칠 수 있다.

⚠ 이 도구는 표가 **의도대로 도는가**를 본다. 표의 **내용이 옳은가**는 담당자만 안다.

사용:
    python scripts/check_org_policy_rules.py datasets/mapping_tables/POLICY_TEMPLATE.json
    python scripts/check_org_policy_rules.py <파일> --json reports/POLICY_RULES.json
"""
from __future__ import annotations

import argparse
import collections
import io
import json
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "src"))

from koipa.modules.m3_labeling.policy_engine import (  # noqa: E402
    Policy,
    Rule,
    evaluate,
    load,
    validate,
)

# 조건을 충족/위반시키는 값. 사실마다 그럴듯한 값을 둔다 — 값 자체는 판정에 안 쓰이고
# 조건을 넘기는 데만 쓰인다.
_SAMPLES: dict[str, tuple] = {
    "public_disclosed": (True, False),
    "has_concrete_parameters": (True, False),
    "content_kinds": (["가격", "원가", "입찰", "설계도", "소스코드"], ["일반 안내"]),
    "legal_protection_basis": ("국가핵심기술 지정 제00호", ""),
    "security_marking": ("top_secret", "none"),
    "access_scope": ("approved_only", "all_employees"),
    "owner_org": ("연구개발", ""),
    "dlp_label": ("restricted", ""),
    "actual_reader_scope": ("department", "all_employees"),
}


def _satisfy(fact: str, spec: dict):
    """이 조건을 **충족**시키는 값."""
    op, want = str(spec.get("op", "eq")), spec.get("value")
    if op == "eq":
        return want
    if op == "ne":
        return _other_than(fact, want)
    if op in ("in", "contains_any"):
        first = (want or [None])[0]
        return [first] if op == "contains_any" else first
    if op == "not_in":
        return _other_than(fact, (want or [None])[0])
    if op == "gte":
        return want
    if op == "lte":
        return want
    if op == "exists":
        return _SAMPLES.get(fact, ("값", ""))[0]
    if op == "missing":
        return None
    return _SAMPLES.get(fact, ("값", ""))[0]


def _violate(fact: str, spec: dict):
    """이 조건을 **어기는** 값(없으면 None 을 돌려 '못 만든다'로 본다)."""
    op, want = str(spec.get("op", "eq")), spec.get("value")
    if op == "eq":
        return _other_than(fact, want)
    if op == "ne":
        return want
    if op in ("in", "contains_any"):
        other = _other_than(fact, (want or [None])[0])
        return [other] if op == "contains_any" else other
    if op == "not_in":
        first = (want or [None])[0]
        return first
    if op == "exists":
        return None  # 값이 없으면 exists 가 깨진다 — 다만 이건 '증거 미비'와 겹친다
    if op == "missing":
        return _SAMPLES.get(fact, ("값", ""))[0]
    return None


def _other_than(fact: str, value):
    for candidate in _SAMPLES.get(fact, ("값1", "값2")):
        if candidate != value:
            return candidate
    return "다른값"


def _facts_for(rule: Rule) -> dict:
    facts = {f: _satisfy(f, s) for f, s in rule.when.items()}
    for ev in rule.requires_evidence:
        facts.setdefault(ev, _SAMPLES.get(ev, ("값", ""))[0])
    return {k: v for k, v in facts.items() if v is not None}


def check(policy: Policy) -> dict:
    out: dict = {"rules": [], "priority_conflicts": [], "unreachable": [], "inert_conditions": []}

    by_priority: dict[int, list[Rule]] = collections.defaultdict(list)
    for rule in policy.rules:
        by_priority[rule.priority].append(rule)
    for prio, group in sorted(by_priority.items()):
        grades = {r.grade for r in group}
        if len(group) > 1 and len(grades) > 1:
            out["priority_conflicts"].append(
                {"priority": prio, "rules": [r.id for r in group], "grades": sorted(grades)})

    for rule in policy.rules:
        facts = _facts_for(rule)
        got = evaluate(policy, facts)
        row = {"rule": rule.id, "grade": rule.grade, "facts": facts,
               "got_rule": got.rule_id, "got_grade": got.grade,
               "reachable": got.rule_id == rule.id}
        if not row["reachable"]:
            out["unreachable"].append(
                {"rule": rule.id, "shadowed_by": got.rule_id, "grade": rule.grade})

        # 조건을 하나씩 깨뜨려 본다 — 깨도 결과가 같으면 그 조건은 실제로 안 쓰인다.
        inert = []
        for fact, spec in rule.when.items():
            bad = _violate(fact, spec)
            probe = dict(facts)
            if bad is None:
                probe.pop(fact, None)
            else:
                probe[fact] = bad
            if evaluate(policy, probe).rule_id == rule.id:
                inert.append(fact)
        if inert and row["reachable"]:
            out["inert_conditions"].append({"rule": rule.id, "conditions": inert})
        row["inert_conditions"] = inert

        # 증거를 하나씩 빼 본다 — 하향되면 안 된다.
        evidence_rows = []
        for ev in rule.requires_evidence:
            probe = {k: v for k, v in facts.items() if k != ev}
            res = evaluate(policy, probe)
            downgraded = (res.grade is not None and policy.severity(res.grade) >= 0
                          and policy.severity(res.grade) < policy.severity(rule.grade)
                          and not res.needs_review)
            evidence_rows.append({"evidence": ev, "grade": res.grade,
                                  "needs_review": res.needs_review, "silent_downgrade": downgraded})
        row["evidence"] = evidence_rows
        out["rules"].append(row)
    return out


def main(argv=None) -> int:
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="회원사 정책표 규칙 시험")
    ap.add_argument("path")
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)

    policy = load(a.path)
    issues = validate(policy)
    if issues:
        print("⛔ 표 자체가 아직 안 맞습니다 — 먼저 고치십시오(%d건)" % len(issues))
        for i in issues:
            print("  · %s" % i)
        return 1

    rep = check(policy)
    print("정책 %s · 시행일 %s · 규칙 %d건"
          % (policy.version, policy.effective_date, len(policy.rules)))
    print("등급 순서: %s\n" % " > ".join(policy.grade_order))

    silent = [(r["rule"], e["evidence"]) for r in rep["rules"]
              for e in r["evidence"] if e["silent_downgrade"]]

    print("%-8s %-10s %-8s %s" % ("규칙", "등급", "도달", "증거 빠졌을 때"))
    print("-" * 72)
    for r in rep["rules"]:
        ev = " · ".join("%s→%s%s" % (e["evidence"], e["grade"] or "없음",
                                     "(검수)" if e["needs_review"] else "")
                        for e in r["evidence"]) or "(요구 증거 없음)"
        print("%-8s %-10s %-8s %s"
              % (r["rule"], r["grade"], "○" if r["reachable"] else "✕ 가려짐", ev))

    print()
    if rep["unreachable"]:
        print("⛔ **도달 불가 규칙 %d건** — 조건이 맞아도 상위 규칙에 가려져 한 번도 안 쓰입니다"
              % len(rep["unreachable"]))
        for u in rep["unreachable"]:
            print("   %s (%s) ← %s 가 먼저 잡습니다" % (u["rule"], u["grade"], u["shadowed_by"]))
    if rep["priority_conflicts"]:
        print("⛔ **우선순위 충돌 %d건** — 같은 순위에 다른 등급이라 무엇이 이기는지 표가 안 정합니다"
              % len(rep["priority_conflicts"]))
        for c in rep["priority_conflicts"]:
            print("   priority %s: %s → %s" % (c["priority"], ", ".join(c["rules"]),
                                               ", ".join(c["grades"])))
    if rep["inert_conditions"]:
        print("⚠ **작동하지 않는 조건** — 깨뜨려도 결과가 같습니다(다른 조건이 이미 가릅니다)")
        for c in rep["inert_conditions"]:
            print("   %s: %s" % (c["rule"], ", ".join(c["conditions"])))
    if silent:
        print("⛔ **증거가 없는데 조용히 하향됩니다 %d건** — 이것이 곧 미탐입니다" % len(silent))
        for rule_id, ev in silent:
            print("   %s: %s 가 없을 때" % (rule_id, ev))
    if not (rep["unreachable"] or rep["priority_conflicts"] or rep["inert_conditions"] or silent):
        print("✅ 구멍 없음 — 규칙 %d건 전부 도달 가능 · 증거 미비 시 하향 없음" % len(policy.rules))

    if a.json:
        target = _POC / a.json if not Path(a.json).is_absolute() else Path(a.json)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(rep, ensure_ascii=False, indent=1),
                          encoding="utf-8", newline="\n")
        print("\n기록: %s" % target)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
