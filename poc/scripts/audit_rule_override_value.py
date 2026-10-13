# -*- coding: utf-8 -*-
"""룰의 실제 생산 역할 — FNR-safe 상향이 값어치를 하는가를 잰다.

왜(2026-08-29). 룰엔진의 등급(argmax)은 최종 등급을 정하지 않는다. 서빙에서 룰이 등급을
움직이는 경로는 **등급별 원점수 임계**다(m5_inference/pipeline.py:690):

    rule grade_scores["TS"] >= 3.0  → 최종 TS
    elif  ["S1"] >= 2.2             → 최종 S1
    elif  ["S2"] >= 1.6             → 최종 S2
    (모델 등급보다 높을 때만 적용 = 상향 전용)

따라서 "룰 공식이 최선인가"는 argmax 정확도가 아니라 **이 상향이 미탐을 실제로 줄이는가**로
판정해야 한다. 이 스크립트가 그것을 센다 — 모델 원등급 · 상향 후 등급 · 정답을 3자 대조한다.

측정:
    상향 발동      몇 건에서 상향이 걸렸나
    구제(rescue)   모델이 정답보다 낮게 봤는데 상향이 정답 이상으로 올린 건수 (이득)
    과상향         모델이 이미 맞았는데 상향이 정답 위로 올린 건수 (검수 비용)
    무해           상향했으나 여전히 정답보다 낮음 (미탐 유지)
    임계 민감도    TS/S1/S2 임계를 바꿨을 때 구제·과상향이 어떻게 움직이나

사용:
    TESTING=1 python scripts/audit_rule_override_value.py [--sets hardened42,clean42,...]
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # noqa: BLE001
    pass

GRADES = ("TS", "S1", "S2", "S3")
RANK = {g: i for i, g in enumerate(GRADES)}      # TS=0 이 가장 높다

DEFAULT_MODEL = "artifacts/classifier_p1_v5_clean/v-fe4b386b"

EVAL_SETS = {
    "hardened42": "datasets/gold_real/holdout_eval.hardened.jsonl",
    "clean42": "datasets/gold_real/holdout_eval.clean.jsonl",
    "business35": "datasets/gold_real/holdout_business.clean.jsonl",
    "holdout109": "datasets/gold_real/_rejudge_claude/holdout109_provenance_corrected.jsonl",
    "v3_final800": "datasets/proxy_eval/direct_authored_proxy_eval_split.v3/final_800.locked.jsonl",
}

_OVR = re.compile(r"fnr-safe override: rule (?P<to>\w+) score=(?P<score>[\d.]+) .*?\(model (?P<from>\w+)")


def classify_case(truth: str, model_g: str, final_g: str) -> str:
    """상향 1건을 이득/비용으로 분류한다."""
    t, m, f = RANK[truth], RANK.get(model_g, 9), RANK.get(final_g, 9)
    if m > t and f <= t:
        return "rescue"        # 모델 미탐 → 정답 이상으로 구제
    if m > t and f > t:
        return "still_under"   # 올렸지만 여전히 미탐
    if m <= t and f < t:
        return "over"          # 이미 맞거나 높았는데 더 올림 = 과상향
    return "neutral"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sets", default="hardened42,clean42,business35,holdout109")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args(argv)

    from koipa.modules.m5_inference.pipeline import InferencePipeline
    from koipa.modules.m3_labeling.rule_engine import LabelRuleEngine
    from koipa.modules.m3_labeling.seeds import KEYWORD_SEEDS

    pipe = InferencePipeline(model_dir=_ROOT / args.model)
    if pipe._model is None:
        print(f"모델을 못 읽었다: {args.model} — 측정 불가")
        return 2
    print(f"모델 {args.model} · 보정출처 {pipe._calibration_source} · T={pipe._temperature:.3f}")
    print(f"임계 TS>={pipe._FNR_RULE_TS_THRESHOLD} S1>={pipe._FNR_RULE_S1_THRESHOLD} "
          f"S2>={pipe._FNR_RULE_S2_THRESHOLD}\n")

    rule = LabelRuleEngine(seeds=KEYWORD_SEEDS)
    # 현행(3.0/2.2/1.6)을 가운데 두고 위·아래로 벌려 본다. 999=상향 완전 해제.
    thresholds = [(999, 999, 999), (6.0, 5.0, 4.0), (4.5, 3.5, 2.5), (3.0, 2.2, 1.6),
                  (2.0, 1.5, 1.0), (1.5, 1.0, 0.8), (1.0, 0.7, 0.5)]

    for name in args.sets.split(","):
        rel = EVAL_SETS.get(name.strip())
        if not rel or not (_ROOT / rel).exists():
            print(f"[{name}] 없음")
            continue
        rows = [json.loads(x) for x in (_ROOT / rel).read_text("utf-8").splitlines() if x.strip()]
        n = len(rows)
        cnt = collections.Counter()
        acc_model = acc_final = 0
        sev_model = sev_final = 0
        scores_cache = []

        for r in rows:
            text = r.get("text") or ""
            truth = r.get("label")
            res = pipe.run(text, return_evidence=False)
            final_g = res.label.value if hasattr(res.label, "value") else str(res.label)
            model_g = final_g
            for w in res.warnings:
                mo = _OVR.search(w)
                if mo:
                    model_g = mo.group("from")
                    cnt[classify_case(truth, model_g, final_g)] += 1
                    cnt["fired"] += 1
                    break
            acc_model += (model_g == truth)
            acc_final += (final_g == truth)
            sev_model += (truth in ("TS", "S1") and model_g in ("S2", "S3"))
            sev_final += (truth in ("TS", "S1") and final_g in ("S2", "S3"))
            gs = rule.label(text).grade_scores
            scores_cache.append((truth, model_g, gs))

        print(f"[{name}] N={n}")
        print(f"   모델 원등급      일치 {acc_model/n:6.1%}   실질미탐 {sev_model}")
        print(f"   룰 상향 적용후   일치 {acc_final/n:6.1%}   실질미탐 {sev_final}"
              f"   (Δ일치 {(acc_final-acc_model)/n:+.1%} · Δ실질미탐 {sev_final-sev_model:+d})")
        print(f"   상향 발동 {cnt['fired']}건 → 구제 {cnt['rescue']} · 과상향 {cnt['over']}"
              f" · 올렸지만 미탐유지 {cnt['still_under']} · 무해 {cnt['neutral']}")

        print("   임계 민감도 (모델 원등급 고정, 임계만 바꿔 재적용):")
        for ts_t, s1_t, s2_t in thresholds:
            c = collections.Counter()
            for truth, model_g, gs in scores_cache:
                new = model_g
                if gs.get("TS", 0) >= ts_t:
                    cand = "TS"
                elif gs.get("S1", 0) >= s1_t:
                    cand = "S1"
                elif gs.get("S2", 0) >= s2_t:
                    cand = "S2"
                else:
                    cand = None
                if cand and RANK[cand] < RANK.get(model_g, 9):
                    new = cand
                if new != model_g:
                    c[classify_case(truth, model_g, new)] += 1
                    c["fired"] += 1
                c["hit"] += (new == truth)
                c["sev"] += (truth in ("TS", "S1") and new in ("S2", "S3"))
            tag = "  ← 현행" if (ts_t, s1_t, s2_t) == (3.0, 2.2, 1.6) else ("  (상향 해제)" if ts_t == 999 else "")
            print(f"      TS>={ts_t} S1>={s1_t} S2>={s2_t}  발동 {c['fired']:>3}"
                  f"  구제 {c['rescue']:>3}  과상향 {c['over']:>3}"
                  f"  일치 {c['hit']/n:6.1%}  실질미탐 {c['sev']:>3}{tag}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
