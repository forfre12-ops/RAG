#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""정책을 **어떻게 써야** 효과가 있나 — 규칙 구성별 민감도.

■ 왜 이 도구가 따로 필요한가 (2026-09-13)

`measure_policy_blocking.py` 가 "ACL 70% 이상이 실용 하한" 이라는 값을 냈는데,
그 수치는 **내가 임의로 쓴 가상 정책 한 벌에 딸린 것**이다. 규칙을 ACL 에만 걸면
ACL 이 없을 때 전부 보류되는 게 당연하다. 규칙 구성을 바꾸면 답이 달라진다.

고객사에게 "정책을 이렇게 쓰세요" 를 말하려면 **구성별로 재야** 한다.

■ 네 가지 구성을 같은 4단계 체계로 비교한다

    A  ACL 전용        상위 규칙이 전부 access_scope·security_marking 을 요구
    B  본문 전용        관리표시·가격정보 등 본문에서 관측되는 것만
    C  혼합 폴백        ACL 이 있으면 쓰고, 없으면 본문 사실로 낮은 확신 제안
    D  느슨            requires_evidence 를 걸지 않는다 (보류를 줄인다)

■ 함께 보는 위험 지표

제안율만 보면 D 가 가장 좋아 보인다. 그러나 D 는 **증거를 요구하지 않아서** 제안이
나오는 것이다. 그래서 `증거요구 없이 나온 제안` 비율을 함께 센다. 이 값이 높으면
근거 없는 등급이 늘어난 것이고, 그것이 곧 미탐 위험이다.

⛔ 미탐 자체는 여기서 못 잰다 — 고객사 정답이 없다. 대리 지표임을 잊지 말 것.

사용:
    python scripts/measure_policy_design_sensitivity.py
    python scripts/measure_policy_design_sensitivity.py --n 2000 --json out.json
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "src"))
sys.path.insert(0, str(_POC / "scripts"))

try:
    from _cli_io import force_utf8_stdio
except ImportError:
    from scripts._cli_io import force_utf8_stdio

force_utf8_stdio()

from koipa.modules.m3_labeling.policy_engine import Policy, Rule, evaluate  # noqa: E402

ORDER = ("TS", "S1", "S2", "S3")


def _pol(org: str, rules: tuple[Rule, ...]) -> Policy:
    return Policy(org_id=org, version="v1", effective_date="2026-09-13",
                  grade_order=ORDER, default_grade="S3", rules=rules)


VARIANTS = {
    "A. ACL 전용": _pol("A", (
        Rule("R-01", "TS", 10, {"security_marking": {"op": "eq", "value": "top_secret"}}, ("security_marking",)),
        Rule("R-02", "S1", 20, {"security_marking": {"op": "eq", "value": "secret"}}, ("security_marking",)),
        Rule("R-03", "S1", 25, {"access_scope": {"op": "eq", "value": "approved_only"}}, ("access_scope",)),
        Rule("R-04", "S2", 40, {"access_scope": {"op": "in", "value": ["designated", "department"]}}, ("access_scope",)),
    )),
    "B. 본문 전용": _pol("B", (
        Rule("R-01", "TS", 10, {"management_marking_in_text": {"op": "eq", "value": True},
                                "has_price_or_cost": {"op": "eq", "value": True}}, ()),
        Rule("R-02", "S1", 25, {"management_marking_in_text": {"op": "eq", "value": True}}, ()),
        Rule("R-03", "S2", 40, {"has_price_or_cost": {"op": "eq", "value": True}}, ()),
    )),
    "C. 혼합 폴백": _pol("C", (
        Rule("R-01", "TS", 10, {"security_marking": {"op": "eq", "value": "top_secret"}}, ("security_marking",)),
        Rule("R-02", "S1", 20, {"access_scope": {"op": "eq", "value": "approved_only"}}, ("access_scope",)),
        # ACL 이 없을 때 쓰는 낮은 확신 경로 — 증거를 요구하지 않는다
        Rule("R-03", "S1", 30, {"management_marking_in_text": {"op": "eq", "value": True},
                                "has_price_or_cost": {"op": "eq", "value": True}}, ()),
        Rule("R-04", "S2", 40, {"management_marking_in_text": {"op": "eq", "value": True}}, ()),
        Rule("R-05", "S2", 50, {"has_price_or_cost": {"op": "eq", "value": True}}, ()),
    )),
    "D. 느슨(증거 요구 없음)": _pol("D", (
        Rule("R-01", "TS", 10, {"security_marking": {"op": "eq", "value": "top_secret"}}, ()),
        Rule("R-02", "S1", 20, {"access_scope": {"op": "eq", "value": "approved_only"}}, ()),
        Rule("R-03", "S1", 25, {"management_marking_in_text": {"op": "eq", "value": True}}, ()),
        Rule("R-04", "S2", 40, {"has_price_or_cost": {"op": "eq", "value": True}}, ()),
    )),
}

MARKINGS = ("top_secret", "secret", "confidential", "none")
SCOPES = ("approved_only", "designated", "department", "all_employees")


def make_facts(rng: random.Random, coverage: float) -> dict:
    """안 오는 값은 **키를 넣지 않는다** — None/False 로 채우면 '부재 확인'이 되어 하향한다."""
    f: dict = {}
    if rng.random() < coverage:
        f["security_marking"] = rng.choice(MARKINGS)
    if rng.random() < coverage:
        f["access_scope"] = rng.choice(SCOPES)
    if rng.random() < 0.18:            # 실측 탐지율 11~26%
        f["management_marking_in_text"] = True
    if rng.random() < 0.25:
        f["has_price_or_cost"] = True
    return f


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="정책 규칙 구성별 민감도")
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20260913)
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)

    covs = (0.0, 0.30, 0.70, 1.0)
    out: dict = {"n": a.n, "seed": a.seed, "variants": {}}
    w = sys.stdout.write
    w("=" * 92 + "\n")
    w(" 정책을 어떻게 써야 효과가 있나 — 규칙 구성별 (같은 4단계 체계 · grade=null 안 기준)\n")
    w("=" * 92 + "\n")
    w(f"  표본 {a.n}건 · seed {a.seed} · 본문 관측률은 실측값(관리표시 18% · 가격 25%) 고정\n\n")
    w(f"  {'구성':24s} {'ACL':>5s} {'제안':>7s} {'검수':>7s} {'증거없이 제안':>13s}\n")
    w("  " + "-" * 88 + "\n")

    for name, pol in VARIANTS.items():
        out["variants"][name] = {}
        # 증거를 요구하지 않는 규칙 id
        no_ev = {r.id for r in pol.rules if not r.requires_evidence}
        for cov in covs:
            rng = random.Random(a.seed + int(cov * 100))
            prop = rev = weak = 0
            for _ in range(a.n):
                p = evaluate(pol, make_facts(rng, cov))
                suppress = bool(p.blocked_rules) and (p.rule_id is None or p.needs_review)
                if not suppress and p.grade is not None:
                    prop += 1
                    if p.rule_id in no_ev:
                        weak += 1
                if p.needs_review or suppress:
                    rev += 1
            out["variants"][name][f"{cov:.0%}"] = {
                "proposed": prop, "needs_review": rev, "proposed_without_evidence": weak}
            w(f"  {name:24s} {cov:4.0%} {prop / a.n:6.1%} {rev / a.n:6.1%} "
              f"{weak / prop * 100 if prop else 0:11.1f}%\n")
        w("\n")

    w("  읽는 법\n")
    w("   · '증거없이 제안' = 그 제안이 requires_evidence 를 걸지 않은 규칙에서 나온 비율.\n")
    w("     높을수록 근거 없는 등급이 많다 — **미탐 위험의 대리 지표**다(미탐 자체는 못 잰다).\n")
    w("   · 제안율만 보고 고르면 D 를 고르게 된다. 세 값을 함께 볼 것.\n")
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        w(f"\n[saved] {a.json}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
