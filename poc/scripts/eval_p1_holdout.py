"""Evaluate a P1 model on the clean holdout, broken down by gold tier.

Loads the model once and reports F1/FNR for: ALL, legally_grounded, llm_judge.
Use to compare the contaminated model (trained on these docs) against a
decontaminated model (holdout removed from train) on the SAME holdout — the
gap is the train-on-test inflation.

[라벨 경화 인식] --holdout 이 trust_tier 필드를 가진 경화 셋
(holdout_eval.hardened.jsonl)을 가리키면, CONSENSUS(anchor+adjudicated, 권장
평가 진실) / QUARANTINE(S2 dead-zone, strict 메트릭 제외)을 분리 보고하고,
모든 섹션에 per-grade N과 truth_level 배너(사람서명 아님)를 붙인다. 단일 숫자
보고 금지 — N 없는 F1은 의미 없음(특히 경화 후 S2 anchor=0). 경화 근거:
scripts/harden_eval_labels.py.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from eval_p1_model_gold import compute_metrics, predict_direct  # noqa: E402

# 외부권위만 legally_grounded(단일 진실원=koipa.golden_tiers). koipa는 2026-07-03 강등
# (판례 인용 조작 확인 — 손작성 시나리오) → curated_scenario로 분리 보고.
from koipa.golden_tiers import EXTERNAL_AUTHORITY_SOURCES as LEGAL  # noqa: E402

CURATED = {"koipa_case_based", "curated_scenario"}
LLMJ = {"llm_judge_primary", "llm_judge_consensus", "codex_review"}


def _tier(ls: str) -> str:
    if ls in LEGAL:
        return "legally_grounded"
    if ls in CURATED:
        return "curated_scenario"
    if ls in LLMJ:
        return "llm_judge"
    return "other"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--holdout", default="datasets/gold_real/holdout_eval.clean.jsonl")  # clean=train 누출 제거(dirty 109건 중 67건=61%가 train_subset 중복 → 암기 부풀림)
    ap.add_argument("--report", default="reports/p1_holdout_eval.json")
    args = ap.parse_args()

    rows = [
        json.loads(l)
        for l in Path(args.holdout).read_text(encoding="utf-8").splitlines()
        if l.strip() and not l.startswith("#")
    ]
    rows = [r for r in rows if r.get("label") in {"TS", "S1", "S2", "S3"}]
    preds = predict_direct(Path(args.model_dir), rows)
    for r, p in zip(rows, preds):
        r["_pred"] = p["label"]

    def _grade_n(subset: list[dict]) -> dict:
        from collections import Counter
        c = Counter(r["label"] for r in subset)
        return {g: c.get(g, 0) for g in ("TS", "S1", "S2", "S3")}

    def metrics_for(subset: list[dict]) -> dict:
        if not subset:
            return {"n": 0}
        m = compute_metrics([r["label"] for r in subset], [r["_pred"] for r in subset])
        gn = _grade_n(subset)
        # [정직] 4클래스 macro는 0표본 등급의 f1=0이 점수를 끌어내려(완벽분류도 S2=0이면 0.75 캡)
        # 오해를 부른다. 표본 있는 등급만의 macro를 별도 보고.
        present = [g for g in ("TS", "S1", "S2", "S3") if m["per_class"][g]["support"] > 0]
        f1_present = round(sum(m["per_class"][g]["f1"] for g in present) / len(present), 4) if present else 0.0
        return {
            "n": m["n"],
            "per_grade_n": gn,  # [정직] N 없는 F1 금지 — 등급별 표본 수를 항상 노출
            "coverage_gap": [g for g, k in gn.items() if k == 0],  # 0표본 등급(평가 불가)
            "f1_macro": m["f1_macro"],
            "f1_macro_present_classes": f1_present,  # 표본 있는 등급만 — 부분 커버리지에서 정직한 값
            "fnr_underclass": m["fnr_underclass"],
            "high_risk_to_s3": m["high_risk_to_s3"],
            "per_class_recall": {g: m["per_class"][g]["recall"] for g in ("TS", "S1", "S2", "S3")},
        }

    has_tiers = any("trust_tier" in r for r in rows)
    out = {
        "model_dir": args.model_dir,
        "holdout": args.holdout,
        "ALL": metrics_for(rows),
        "legally_grounded": metrics_for([r for r in rows if _tier(r.get("label_source", "")) == "legally_grounded"]),
        "curated_scenario": metrics_for([r for r in rows if _tier(r.get("label_source", "")) == "curated_scenario"]),
        "llm_judge": metrics_for([r for r in rows if _tier(r.get("label_source", "")) == "llm_judge"]),
    }
    if has_tiers:
        # 경화 셋: 신뢰 평가는 CONSENSUS만, QUARANTINE은 분리(strict 제외)
        consensus = [r for r in rows if r.get("trust_tier") in ("anchor", "adjudicated")]
        quarantine = [r for r in rows if r.get("trust_tier") == "quarantine"]
        out["_truth_level"] = "machine_adjudicated_silver — 사람서명 아님. 종단 평가 진실 불가(합성 koipa 시나리오·N 소). 추세·회귀 신호로만."
        out["CONSENSUS"] = metrics_for(consensus)        # 권장 헤드라인
        out["QUARANTINE"] = {**metrics_for(quarantine), "_note": "S2/S3 dead-zone — strict 메트릭 제외, 사람 앵커 대기"}
        out["trust_tier_n"] = {t: sum(1 for r in rows if r.get("trust_tier") == t) for t in ("anchor", "adjudicated", "quarantine")}
        if out["CONSENSUS"].get("coverage_gap"):
            out["_coverage_warning"] = f"신뢰 평가셋 0표본 등급={out['CONSENSUS']['coverage_gap']} — 해당 등급 성능 측정 불가"
    Path(args.report).parent.mkdir(parents=True, exist_ok=True)
    Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
