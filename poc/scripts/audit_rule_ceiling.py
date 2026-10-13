# -*- coding: utf-8 -*-
"""집계식을 아무리 바꿔도 넘을 수 없는 천장을 잰다 — 매칭 계층의 한계 측정.

왜(2026-08-29). `audit_rule_alternatives.py` 로 집계식 6종을 비교했더니 **실질미탐
(정답 TS·S1 을 S2·S3 로 봄) 건수가 6종 전부 같았다.** 그러면 원인은 집계식이 아니라
그 앞 단계(사전 매칭)라는 뜻이다. 이 스크립트가 그것을 직접 확인한다.

측정:
    ① oracle 천장   정답 등급의 시드가 하나라도 매치된 문서 비율 (+ 정답 S3 는 무매칭
                    기본값으로 도달 가능하므로 포함)
                    = 어떤 집계식으로도 정답을 고를 수 있는 상한
    ② 실질미탐 해부  TS·S1 정답을 놓친 문서에서 TS·S1 시드가 몇 개 떴는가
    ③ 발화 시드     셋별로 실제로 뜬 시드 상위 목록 (사전이 무엇을 보고 있는가)

사용:
    TESTING=1 python scripts/audit_rule_ceiling.py
"""
from __future__ import annotations

import collections
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

GRADES = ("TS", "S1", "S2", "S3")
RANK = {g: i for i, g in enumerate(GRADES)}

EVAL_SETS = {
    "hardened42": "datasets/gold_real/holdout_eval.hardened.jsonl",
    "clean42": "datasets/gold_real/holdout_eval.clean.jsonl",
    "business35": "datasets/gold_real/holdout_business.clean.jsonl",
    "holdout109": "datasets/gold_real/_rejudge_claude/holdout109_provenance_corrected.jsonl",
    "v3_final800": "datasets/proxy_eval/direct_authored_proxy_eval_split.v3/final_800.locked.jsonl",
}


def main() -> int:
    from koipa.modules.m3_labeling.rule_engine import LabelRuleEngine
    from koipa.modules.m3_labeling.seeds import KEYWORD_SEEDS

    engine = LabelRuleEngine(seeds=KEYWORD_SEEDS)

    for set_name, rel in EVAL_SETS.items():
        path = _ROOT / rel
        if not path.exists():
            print(f"\n[{set_name}] 없음: {rel}")
            continue
        rows = [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]
        n = len(rows)
        reachable = 0                      # 정답 등급 시드가 1개 이상 뜬 문서
        severe_docs = 0                    # 정답 TS·S1 인데 예측 S2·S3
        severe_no_hit = 0                  # 그 중 TS·S1 시드가 0개 뜬 문서
        seed_fire = collections.Counter()
        by_grade_hit = collections.Counter()

        for r in rows:
            truth = r.get("label")
            res = engine.label(r.get("text") or "")
            hits = collections.Counter(mk.grade for mk in res.matched_keywords)
            for mk in res.matched_keywords:
                seed_fire[(mk.grade, mk.keyword)] += 1
            # S3 는 시드 없이도 무매칭 기본값으로 도달한다 — 천장 계산에 포함해야 한다.
            if hits.get(truth, 0) > 0 or truth == "S3":
                reachable += 1
            for g in GRADES:
                if hits.get(g, 0) > 0:
                    by_grade_hit[g] += 1
            if truth in ("TS", "S1") and res.grade in ("S2", "S3"):
                severe_docs += 1
                if hits.get("TS", 0) + hits.get("S1", 0) == 0:
                    severe_no_hit += 1

        print(f"\n[{set_name}] N={n}")
        print(f"   ① oracle 천장 (정답등급 시드 발화 또는 정답 S3)  {reachable}/{n} = {reachable/n:.1%}")
        print("      → 어떤 집계식도 이 위로 못 간다")
        print(f"   ② 실질미탐 {severe_docs}건 중 TS·S1 시드가 0개 뜬 문서  {severe_no_hit}건"
              + (f" ({severe_no_hit/severe_docs:.0%})" if severe_docs else ""))
        print("   등급별 시드 발화 문서수: "
              + " ".join(f"{g}:{by_grade_hit.get(g,0)}" for g in GRADES))
        top = seed_fire.most_common(6)
        print("   ③ 상위 발화 시드: " + " · ".join(f"[{g}]{k}×{c}" for (g, k), c in top))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
