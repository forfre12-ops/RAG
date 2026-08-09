"""서빙경로 평가 드라이버 — C-eval/C-esc 실측 (doc/36).

evaluate_via_serving으로 홀드아웃을 **실서빙 InferencePipeline.run() 전체 경로**
(temperature 보정·청크 most-severe 집계·FNR-safe override·source-prior 게이트·escalation τ)로
통과시켜 지표를 측정한다. 원시 argmax 스윕(eval_fnr_threshold_sweep.py)과 달리 **실배포 동작**을
측정하므로 "측정값=배포동작" 정합(C-eval)을 보장하고, τ를 바꿔가며 escalation 효과(C-esc)를 본다.

사용:
  python scripts/eval_serving_path.py \
    --model-dir artifacts/classifier_p1_v5_clean/v-fe4b386b \
    --gold datasets/gold_real/holdout_eval.jsonl \
    --taus 0,0.35,0.25,0.15 \
    --report reports/serving_path_eval.md
  ( --taus의 0 = τ 미적용(순수 argmax, 기본 동작) )
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

LABELS = ["TS", "S1", "S2", "S3"]


def load_rows(path: Path) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        r = json.loads(line)
        label = r.get("label") or r.get("expected_grade") or r.get("target")
        text = r.get("text") or r.get("body") or ""
        if label in LABELS and text:
            rows.append({"text": text, "label": label})
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--gold", default="datasets/gold_real/holdout_eval.clean.jsonl")  # clean=train 누출 제거(dirty 109건 중 67건=61%가 train_subset 중복 → 암기 부풀림)
    ap.add_argument("--taus", default="0,0.35,0.25,0.15", help="쉼표구분. 0=τ미적용(argmax)")
    ap.add_argument("--multipliers", default="",
                    help="쉼표구분 rule_high_risk_weight_multiplier 스윕(B1). 주면 τ=None 고정, "
                         "배수별 서빙 FNR 측정(배수마다 파이프라인 재생성=모델 재로드).")
    ap.add_argument(
        "--offline-default-registry",
        action="store_true",
        help=(
            "Use built-in TS/S1/S2/S3 registry and skip optional local DB keyword "
            "loading when the DB is unreachable. Use for immutable proxy-file "
            "evaluation, not production parity checks."
        ),
    )
    ap.add_argument("--report", default="reports/serving_path_eval.md")
    args = ap.parse_args()

    if args.offline_default_registry:
        os.environ.setdefault("TESTING", "1")

    from lloydk.config import settings
    from lloydk.modules.m5_inference.pipeline import InferencePipeline
    from lloydk.modules.m6_evaluation.serving_eval import evaluate_via_serving

    gold = Path(args.gold)
    if not gold.is_absolute():
        gold = _HERE.parent / gold
    rows = load_rows(gold)

    model_dir = Path(args.model_dir)
    if not model_dir.is_absolute():
        model_dir = _HERE.parent / model_dir

    # ── B1 multiplier 스윕 (옵션) — τ=None 고정, 배수별 서빙 FNR 측정 ───────────────
    # 룰 엔진은 생성 시점에 settings.rule_high_risk_weight_multiplier를 읽으므로, 배수마다
    # 파이프라인을 새로 만들어야 한다(모델 재로드 비용). 목적: 범용약어 배수를 낮춰도
    # 서빙 FNR_TS=0이 유지되는 최저 배수를 찾는다(과분류↓ 하면서 미탐 불변 지점).
    if args.multipliers.strip():
        mults = [float(x) for x in args.multipliers.split(",") if x.strip()]
        saved_m = settings.rule_high_risk_weight_multiplier
        saved_t = settings.classifier_escalation_tau
        settings.classifier_escalation_tau = None
        m_results = []
        try:
            for mult in mults:
                settings.rule_high_risk_weight_multiplier = mult
                p = InferencePipeline(model_dir=str(model_dir))  # 재생성=룰 엔진 배수 반영
                m = evaluate_via_serving(rows, pipeline=p, model_version=f"mult={mult}")
                m_results.append({
                    "multiplier": mult,
                    "accuracy": round(m.accuracy, 4),
                    "f1_macro": round(m.f1_macro, 4),
                    "fnr_underclass_overall": round(m.fnr_underclass_overall, 4),
                    "fnr_TS": round(m.fnr_underclass_by_grade.get("TS", 0.0), 4),
                    "fnr_S1": round(m.fnr_underclass_by_grade.get("S1", 0.0), 4),
                })
        finally:
            settings.rule_high_risk_weight_multiplier = saved_m
            settings.classifier_escalation_tau = saved_t
        out = Path(args.report)
        if not out.is_absolute():
            out = _HERE.parent / out
        md = [
            "# B1 범용약어 배수 스윕 — 서빙경로 FNR (τ=argmax 고정)",
            "", f"- model_dir: `{args.model_dir}`", f"- gold: `{args.gold}` (n={len(rows)})",
            "", "> 배수를 낮추면 과분류↓(검수부하↓)지만 룰 override 약화로 FNR↑ 가능.",
            "> **FNR_TS=0을 유지하는 최저 배수**가 안전한 down-weight 한계.", "",
            "| 배수 | FNR(방향성) | FNR_TS | FNR_S1 | F1 | Acc |",
            "|---:|---:|---:|---:|---:|---:|",
        ]
        for r in m_results:
            md.append(f"| {r['multiplier']:.2f} | {r['fnr_underclass_overall']:.3f} | "
                      f"{r['fnr_TS']:.3f} | {r['fnr_S1']:.3f} | {r['f1_macro']:.3f} | {r['accuracy']:.3f} |")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(md), encoding="utf-8")
        out.with_suffix(".json").write_text(
            json.dumps({"model_dir": args.model_dir, "gold": args.gold, "n": len(rows),
                        "multiplier_sweep": m_results}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps({"n": len(rows), "multiplier_sweep": m_results, "report": str(out)},
                         ensure_ascii=False, indent=2))
        return 0

    # 모델 1회 로드 — τ만 바꿔가며 같은 파이프라인으로 측정(서빙 인스턴스 동일).
    pipe = InferencePipeline(model_dir=str(model_dir))

    taus = []
    for x in args.taus.split(","):
        x = x.strip()
        if not x:
            continue
        v = float(x)
        taus.append(None if v <= 0 else v)

    results = []
    saved_tau = settings.classifier_escalation_tau
    try:
        for tau in taus:
            settings.classifier_escalation_tau = tau
            m = evaluate_via_serving(rows, pipeline=pipe, model_version=f"serving_tau={tau}")
            fnr_high = (m.fnr_underclass_by_grade.get("TS", 0.0) + m.fnr_underclass_by_grade.get("S1", 0.0)) / 2
            results.append({
                "tau": tau,
                "accuracy": round(m.accuracy, 4),
                "f1_macro": round(m.f1_macro, 4),
                "fnr_underclass_overall": round(m.fnr_underclass_overall, 4),
                "fnr_high_avg": round(fnr_high, 4),
                "fnr_TS": round(m.fnr_underclass_by_grade.get("TS", 0.0), 4),
                "fnr_S1": round(m.fnr_underclass_by_grade.get("S1", 0.0), 4),
            })
    finally:
        settings.classifier_escalation_tau = saved_tau

    def tstr(t):
        return "argmax" if t is None else f"{t:.2f}"

    md = [
        "# 서빙경로 평가 — C-eval/C-esc 실측 (InferencePipeline.run 경유)",
        "",
        f"- model_dir: `{args.model_dir}`",
        f"- gold(holdout): `{args.gold}`  (n={len(rows)})",
        "",
        "> 전체 서빙 경로(temperature·청크 most-severe·FNR-override·source-prior·escalation τ)를",
        "> 거쳐 측정 — 원시 argmax 스윕과 달리 **실배포 동작**. τ를 낮출수록 고등급 승격↑(FNR↓).",
        "",
        "| τ | FNR(방향성) | FNR고등급avg | FNR_TS | FNR_S1 | F1 | Acc |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        md.append(
            f"| {tstr(r['tau'])} | {r['fnr_underclass_overall']:.3f} | {r['fnr_high_avg']:.3f} | "
            f"{r['fnr_TS']:.3f} | {r['fnr_S1']:.3f} | {r['f1_macro']:.3f} | {r['accuracy']:.3f} |"
        )

    out = Path(args.report)
    if not out.is_absolute():
        out = _HERE.parent / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8")
    out.with_suffix(".json").write_text(
        json.dumps({"model_dir": args.model_dir, "gold": args.gold, "n": len(rows),
                    "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({"n": len(rows), "results": results, "report": str(out)},
                     ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
