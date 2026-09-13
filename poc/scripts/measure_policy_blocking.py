#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""정책 엔진을 켜면 **얼마나 보류되는가** — 착수 전에 재는 값.

■ 왜 이것부터 재는가 (2026-09-13)

고객사 정책 엔진(`policy_engine.py`)을 서빙에 붙이기 전에, 먼저 답해야 하는 것이 있다.
**지금 사실(facts)이 거의 안 온다.**

    security_marking   0 / 448,448 행 = 0.00%
    access_scope       0 / 448,448 행 = 0.00%
    source_type        0 / 164,587 행 = 0.00%
    본문 관리표시       11~26%  (그나마 합성에서는 TS 편중 — 25.0% 대 1.6%)

증거가 없으면 엔진은 규칙을 **보류**한다(하향하면 그게 곧 미탐이므로 옳은 설계다).
그런데 전부 보류되면 등급 후보가 하나도 안 나오고 전건이 검수로 간다 —
**자동확정률이 무너진다.** 그래서 "ACL 을 몇 % 받아야 쓸 만한가" 를 먼저 알아야 한다.

■ 무엇을 재는가

층위가 다른 가상 정책 3개(3·4·5단계)에 합성 facts 를 ACL 공급률별로 넣어,
제안이 나오는 비율 · 보류 비율 · 검수 필요 비율을 센다.

⛔ 이 수치는 **가상 정책 기준**이다. 실제 고객사 정책은 규칙 수·조건이 다르므로
   절대값이 아니라 **커버리지에 따른 기울기**를 보는 용도다.

사용:
    python scripts/measure_policy_blocking.py
    python scripts/measure_policy_blocking.py --n 2000 --json out.json
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

# 층위가 다른 가상 정책 셋. 매핑표 파일럿에서 실제로 만난 층위다
# (대통령령 3단계 · 성균관대 3단계 · 한국항공대 5단계 · 우리 4단계).
POLICIES = {
    "4단계(우리 체계)": Policy(
        org_id="virt-4", version="v1", effective_date="2026-09-13",
        grade_order=("TS", "S1", "S2", "S3"), default_grade="S3",
        rules=(
            Rule("R-01", "TS", 10, {"security_marking": {"op": "eq", "value": "top_secret"}},
                 ("security_marking",)),
            Rule("R-02", "S1", 20, {"access_scope": {"op": "eq", "value": "approved_only"}},
                 ("access_scope",)),
            Rule("R-03", "S1", 25, {"has_price_or_cost": {"op": "eq", "value": True},
                                    "access_scope": {"op": "in", "value": ["approved_only", "designated"]}},
                 ("access_scope",)),
            Rule("R-04", "S2", 40, {"access_scope": {"op": "eq", "value": "department"}},
                 ("access_scope",)),
            Rule("R-05", "S3", 90, {"public_disclosed": {"op": "eq", "value": True}},
                 ("public_disclosed",)),
        ),
    ),
    "3단계(비밀/대외비/일반)": Policy(
        org_id="virt-3", version="v1", effective_date="2026-09-13",
        grade_order=("비밀", "대외비", "일반"), default_grade="일반",
        rules=(
            Rule("R-01", "비밀", 10, {"security_marking": {"op": "in", "value": ["top_secret", "secret"]}},
                 ("security_marking",)),
            Rule("R-02", "대외비", 30, {"access_scope": {"op": "in", "value": ["approved_only", "designated", "department"]}},
                 ("access_scope",)),
            Rule("R-03", "일반", 90, {"public_disclosed": {"op": "eq", "value": True}},
                 ("public_disclosed",)),
        ),
    ),
    "5단계(1~5등급)": Policy(
        org_id="virt-5", version="v1", effective_date="2026-09-13",
        grade_order=("1등급", "2등급", "3등급", "4등급", "5등급"), default_grade="5등급",
        rules=(
            Rule("R-01", "1등급", 10, {"security_marking": {"op": "eq", "value": "top_secret"}},
                 ("security_marking",)),
            Rule("R-02", "2등급", 20, {"security_marking": {"op": "eq", "value": "secret"}},
                 ("security_marking",)),
            Rule("R-03", "3등급", 30, {"access_scope": {"op": "eq", "value": "approved_only"},
                                       "has_price_or_cost": {"op": "eq", "value": True}},
                 ("access_scope",)),
            Rule("R-04", "4등급", 50, {"access_scope": {"op": "eq", "value": "department"}},
                 ("access_scope",)),
            Rule("R-05", "5등급", 90, {"public_disclosed": {"op": "eq", "value": True}},
                 ("public_disclosed",)),
        ),
    ),
}

MARKINGS = ("top_secret", "secret", "confidential", "none")
SCOPES = ("approved_only", "designated", "department", "all_employees")


def make_facts(rng: random.Random, coverage: float) -> dict:
    """사실 묶음 하나. coverage 확률로 시스템 공급 값이 온다.

    ⚠ 안 오는 것은 **키를 넣지 않는다.** None/False 로 채우면 '부재 확인'이 되어
      엔진이 하향해 버린다 — 바로 그 통로가 미탐이다.
    """
    facts: dict = {}
    if rng.random() < coverage:
        facts["security_marking"] = rng.choice(MARKINGS)
    if rng.random() < coverage:
        facts["access_scope"] = rng.choice(SCOPES)
    # 본문 관측: 관리표시는 실측 탐지율이 11~26% 였다. 가격 정보는 아직 추출기가 없다.
    if rng.random() < 0.18:
        facts["management_marking_in_text"] = True
    if rng.random() < 0.25:
        facts["has_price_or_cost"] = True
    # 공개 여부는 **부정 입증을 하지 않는다** — 공개로 확인된 경우만 True 로 온다.
    if rng.random() < 0.06:
        facts["public_disclosed"] = True
    return facts


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="정책 엔진 보류율 측정")
    ap.add_argument("--n", type=int, default=1000)
    ap.add_argument("--seed", type=int, default=20260913)
    ap.add_argument("--json", default="")
    a = ap.parse_args(argv)

    coverages = (0.0, 0.30, 0.70, 1.0)
    out: dict = {"n": a.n, "seed": a.seed, "coverages": {}}

    w = sys.stdout.write
    w("=" * 88 + "\n")
    w(" 정책 엔진을 켜면 얼마나 보류되는가 — ACL 공급률별\n")
    w("=" * 88 + "\n")
    w(f"  표본 {a.n}건 · 가상 정책 {len(POLICIES)}개(층위 3·4·5단계) · seed {a.seed}\n")
    w("  ⚠ 실측: 지금 security_marking·access_scope·source_type 은 **전부 0.00%** 다.\n")
    w("     즉 현재 상태는 아래 표의 '0%' 줄이다.\n\n")
    w(f"  {'정책':24s} {'공급률':>6s} {'제안 나옴':>9s} {'보류로 없음':>11s} {'검수 필요':>9s}\n")
    w("  " + "-" * 84 + "\n")

    for name, pol in POLICIES.items():
        out["coverages"][name] = {}
        for cov in coverages:
            rng = random.Random(a.seed + int(cov * 100))
            proposed = blocked_none = review = 0
            for _ in range(a.n):
                facts = make_facts(rng, cov)
                p = evaluate(pol, facts)
                if p.grade is None:
                    blocked_none += 1
                else:
                    proposed += 1
                if p.needs_review:
                    review += 1
            row = {"proposed": proposed, "no_grade": blocked_none, "needs_review": review}
            out["coverages"][name][f"{cov:.0%}"] = row
            w(f"  {name:24s} {cov:5.0%} {proposed / a.n:8.1%} {blocked_none / a.n:10.1%} {review / a.n:8.1%}\n")
        w("\n")

    w("  읽는 법\n")
    w("   · '제안 나옴' 은 등급 후보가 나온 비율. 낮으면 정책을 켜도 아무 값이 없다.\n")
    w("   · '검수 필요' 는 would_require_review — 실제 라우팅을 켜면 이만큼 검수로 간다.\n")
    w("   · ⛔ 가상 정책 기준이라 절대값이 아니라 **커버리지에 따른 기울기**를 본다.\n")
    if a.json:
        Path(a.json).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        w(f"\n[saved] {a.json}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
