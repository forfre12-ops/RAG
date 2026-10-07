# -*- coding: utf-8 -*-
"""현행 룰 판정식이 최선인가 — 대안 공식 6종을 같은 매칭 결과 위에서 비교한다.

왜 필요한가(2026-08-29). `audit_rule_formula.py` 는 현행 공식이 무엇을 하는지만 잰다.
"이게 최선의 공식인가" 는 다른 질문이다. 답하려면 **같은 키워드 매칭 결과**에 다른
집계식을 씌워 같은 평가셋에서 재야 한다. 매칭 계층(시드·정규식·부스트)은 고정하고
집계식만 바꾸므로, 차이는 전부 공식 탓이다.

비교 대상(전부 rule_engine.label() 의 matched_keywords 를 그대로 재집계):
    A current      Σ(빈도×가중치) → argmax, 동점은 상위등급   (현행. SVM 단계 포함 결과와 대조)
    B presence     빈도를 1로 눌러 Σ가중치 → argmax           (반복어 지배 제거)
    C normalized   Σ점수 ÷ 그 등급 시드 수 → argmax           (시드 수 편향 제거)
    D maxseed      등급별 최고 단일 시드 점수 → argmax        (최고 심각도 채택)
    E anyhit       매치가 하나라도 있는 최상위 등급           (최대 FNR-safe)
    F pres+norm    B 와 C 를 함께                            (교차 확인)

판정 기준은 요건(RFP 핵심목표)에 맞춘다 — **미탐 최소화**가 1순위, 과분류는 비용.
    under        정답보다 낮게 본 건수 (미탐 방향)
    severe       정답이 TS·S1 인데 S2·S3 로 본 건수 (실질 미탐)
    over         정답보다 높게 본 건수 (검수 부담)

사용:
    TESTING=1 python scripts/audit_rule_alternatives.py
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


def _argmax(scores: dict[str, float]) -> str:
    """동점은 상위 등급(현행 fnr_safe 정책과 동일). 전부 0이면 S3."""
    top = max(scores.values()) if scores else 0.0
    if top <= 0:
        return "S3"
    tops = [g for g, s in scores.items() if s == top]
    return min(tops, key=lambda g: RANK.get(g, 999))


def variants(matches, seed_counts: dict[str, int]) -> dict[str, str]:
    """matched_keywords 하나에서 6개 공식의 등급을 동시에 낸다."""
    cur = {g: 0.0 for g in GRADES}
    pres = {g: 0.0 for g in GRADES}
    mx = {g: 0.0 for g in GRADES}
    hit = {g: 0 for g in GRADES}
    for mk in matches:
        g = mk.grade
        if g not in cur:
            continue
        cur[g] += mk.score
        pres[g] += mk.weight
        mx[g] = max(mx[g], mk.weight)
        hit[g] += 1
    norm = {g: cur[g] / max(seed_counts.get(g, 1), 1) for g in GRADES}
    pnorm = {g: pres[g] / max(seed_counts.get(g, 1), 1) for g in GRADES}
    anyhit = next((g for g in GRADES if hit[g] > 0), "S3")
    return {
        "A current": _argmax(cur),
        "B presence": _argmax(pres),
        "C normalized": _argmax(norm),
        "D maxseed": _argmax(mx),
        "E anyhit": anyhit,
        "F pres+norm": _argmax(pnorm),
    }


def main() -> int:
    from koipa.modules.m3_labeling.rule_engine import LabelRuleEngine
    from koipa.modules.m3_labeling.seeds import KEYWORD_SEEDS

    engine = LabelRuleEngine(seeds=KEYWORD_SEEDS)
    seed_counts = collections.Counter(s["grade"] for s in KEYWORD_SEEDS)
    names = ["A current", "B presence", "C normalized", "D maxseed", "E anyhit", "F pres+norm"]

    for set_name, rel in EVAL_SETS.items():
        path = _ROOT / rel
        if not path.exists():
            print(f"\n[{set_name}] 없음: {rel}")
            continue
        rows = [json.loads(x) for x in path.read_text("utf-8").splitlines() if x.strip()]
        stat = {n: collections.Counter() for n in names}
        dist = {n: collections.Counter() for n in names}
        engine_stat = collections.Counter()
        engine_dist = collections.Counter()

        for r in rows:
            truth = r.get("label")
            res = engine.label(r.get("text") or "")
            preds = variants(res.matched_keywords, seed_counts)
            preds_all = dict(preds)
            preds_all["engine(=A+SVM)"] = res.grade
            for n, p in preds_all.items():
                tgt = stat.get(n) if n in stat else engine_stat
                dst = dist.get(n) if n in dist else engine_dist
                dst[p] += 1
                if p == truth:
                    tgt["hit"] += 1
                elif RANK.get(p, 9) > RANK.get(truth, 9):
                    tgt["under"] += 1
                    if truth in ("TS", "S1") and p in ("S2", "S3"):
                        tgt["severe"] += 1
                else:
                    tgt["over"] += 1

        n = len(rows)
        print(f"\n[{set_name}] N={n}  정답분포 {dict(collections.Counter(r.get('label') for r in rows))}")
        print(f"   {'공식':<16}{'일치':>8}{'미탐':>7}{'실질미탐':>9}{'과분류':>8}   예측분포")
        for name in names:
            s = stat[name]
            d = dist[name]
            print(f"   {name:<16}{s['hit']/n:>7.1%}{s['under']:>7}{s['severe']:>9}{s['over']:>8}   "
                  + " ".join(f"{g}:{d.get(g,0)}" for g in GRADES))
        s, d = engine_stat, engine_dist
        print(f"   {'engine(=A+SVM)':<16}{s['hit']/n:>7.1%}{s['under']:>7}{s['severe']:>9}{s['over']:>8}   "
              + " ".join(f"{g}:{d.get(g,0)}" for g in GRADES))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
