"""FNR 임계 sweep — 재학습 없이 '미탐율을 낮추면 정밀도가 얼마나 깎이는가' 곡선 측정.

배경
----
argmax 결정은 고등급(TS/S1) 확률이 1등이 아니면 저등급으로 떨어뜨려 미탐(FNR)을 만든다.
대신 **severity-ordered escalation**을 쓴다: 임계 τ에 대해
  "가장 심각한 등급 g 중 softmax(g) ≥ τ 인 것"으로 예측하고, 없으면 argmax 폴백.
τ를 낮출수록 고등급으로 더 적극 승격 → FNR↓, 단 과분류(저등급 문서를 고등급으로)↑.

이 스크립트는 모델 추론을 **1회만** 수행해 문서별 전체 softmax 점수를 얻고,
임계 그리드를 순수 파이썬으로 훑어 trade 곡선을 만든다. (GPU/재학습 불필요)

핵심 지표
--------
- fnr_underclass : 진짜 TS/S1 중 더 낮은(덜 심각한) 등급으로 예측된 비율 = 보안 미탐 (낮을수록 좋음)
- high_to_s3     : TS/S1/S2 정답을 S3로 떨군 건수 (치명적 미탐)
- overclass_s3   : 진짜 S3를 고등급으로 올린 비율 = 헛알람의 대가 (낮을수록 좋음)
- review_burden  : TS/S1로 예측된 문서 비율 = 검수 큐 부하 proxy
- f1_macro / accuracy / 등급별 recall

사용
----
  python scripts/eval_fnr_threshold_sweep.py \
    --model-dir artifacts/classifier_p1_v5_clean/v-fe4b386b \
    --gold datasets/gold_real/holdout_eval.jsonl \
    --report reports/fnr_threshold_sweep.md
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

LABELS = ["TS", "S1", "S2", "S3"]
GRADE_ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}   # 숫자 작을수록 심각
HIGH = {"TS", "S1"}                                   # 미탐 핵심 대상

# 기본 임계 그리드: argmax(폴백만) → 점점 공격적 승격
DEFAULT_THRESHOLDS = [0.50, 0.40, 0.35, 0.30, 0.25, 0.20, 0.15, 0.12, 0.10, 0.08, 0.05]


def load_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        row = json.loads(line)
        label = row.get("label") or row.get("expected_grade")
        if label not in LABELS:
            continue
        rows.append({**row, "label": label})
    return rows


def infer_scores(model_dir: Path, rows: list[dict], batch_size: int = 16) -> list[dict[str, float]]:
    """문서별 등급 softmax 점수를 1회 추론으로 산출."""
    import torch
    import torch.nn.functional as F
    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    tok = AutoTokenizer.from_pretrained(str(model_dir))
    model = AutoModelForSequenceClassification.from_pretrained(str(model_dir)).to(device)
    model.eval()
    id2label = {int(k): v for k, v in model.config.id2label.items()}

    texts = [r["text"] for r in rows]
    out: list[dict[str, float]] = []
    with torch.no_grad():
        for start in range(0, len(texts), batch_size):
            batch = texts[start:start + batch_size]
            enc = tok(batch, truncation=True, max_length=512, padding=True, return_tensors="pt").to(device)
            probs = F.softmax(model(**enc).logits, dim=-1).cpu()
            for row_probs in probs:
                out.append({id2label[i]: float(row_probs[i]) for i in range(len(id2label))})
    return out


def predict_with_threshold(scores: dict[str, float], tau: float | None) -> str:
    """severity-ordered escalation. tau=None이면 순수 argmax."""
    if tau is None:
        return max(scores, key=lambda g: scores[g])
    # 가장 심각한 등급부터 검사해 첫 번째로 임계 넘는 등급 채택
    for g in sorted(LABELS, key=lambda x: GRADE_ORDER[x]):
        if scores.get(g, 0.0) >= tau:
            return g
    # 아무것도 임계를 못 넘으면 argmax 폴백
    return max(scores, key=lambda g: scores[g])


def metrics_for(y_true: list[str], y_pred: list[str]) -> dict:
    n = len(y_true)
    cm = {t: {p: 0 for p in LABELS} for t in LABELS}
    for t, p in zip(y_true, y_pred):
        cm[t][p] += 1

    per_class = {}
    for lbl in LABELS:
        tp = cm[lbl][lbl]
        fp = sum(cm[t][lbl] for t in LABELS if t != lbl)
        fn = sum(cm[lbl][p] for p in LABELS if p != lbl)
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
        per_class[lbl] = {"precision": round(prec, 4), "recall": round(rec, 4),
                          "f1": round(f1, 4), "support": sum(cm[lbl].values())}

    high_total = sum(1 for t in y_true if t in HIGH)
    high_under = sum(1 for t, p in zip(y_true, y_pred) if t in HIGH and GRADE_ORDER[p] > GRADE_ORDER[t])
    high_to_s3 = sum(1 for t, p in zip(y_true, y_pred) if t in {"TS", "S1", "S2"} and p == "S3")
    s3_total = sum(1 for t in y_true if t == "S3")
    s3_over = sum(1 for t, p in zip(y_true, y_pred) if t == "S3" and p != "S3")
    review = sum(1 for p in y_pred if p in HIGH)

    return {
        "accuracy": round(sum(t == p for t, p in zip(y_true, y_pred)) / n, 4) if n else 0.0,
        "f1_macro": round(sum(c["f1"] for c in per_class.values()) / len(LABELS), 4),
        "fnr_underclass": round(high_under / high_total, 4) if high_total else 0.0,
        "high_to_s3": high_to_s3,
        "overclass_s3": round(s3_over / s3_total, 4) if s3_total else 0.0,
        "review_burden": round(review / n, 4) if n else 0.0,
        "recall_TS": per_class["TS"]["recall"],
        "recall_S1": per_class["S1"]["recall"],
        "recall_S2": per_class["S2"]["recall"],
        "recall_S3": per_class["S3"]["recall"],
        "per_class": per_class,
        "pred_dist": dict(Counter(y_pred)),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model-dir", required=True)
    ap.add_argument("--gold", default="datasets/gold_real/holdout_eval.clean.jsonl")  # clean=train 누출 제거(dirty 109건 중 67건=61%가 train_subset 중복 → 암기 부풀림)
    ap.add_argument("--report", default="reports/fnr_threshold_sweep.md")
    ap.add_argument("--thresholds", default="", help="쉼표구분 임계 목록 (기본 그리드 사용 시 생략)")
    ap.add_argument("--fnr-target", type=float, default=0.05, help="목표 FNR (운영점 추천용)")
    args = ap.parse_args()

    gold_path = Path(args.gold)
    if not gold_path.is_absolute():
        gold_path = _HERE.parent / gold_path
    rows = load_jsonl(gold_path)
    y_true = [r["label"] for r in rows]

    model_dir = Path(args.model_dir)
    if not model_dir.is_absolute():
        model_dir = _HERE.parent / model_dir
    scores = infer_scores(model_dir, rows)

    thresholds = ([float(x) for x in args.thresholds.split(",") if x.strip()]
                  or DEFAULT_THRESHOLDS)

    sweep = []
    # 1행: argmax 베이스라인
    base_pred = [predict_with_threshold(s, None) for s in scores]
    sweep.append({"tau": None, **metrics_for(y_true, base_pred)})
    for tau in thresholds:
        pred = [predict_with_threshold(s, tau) for s in scores]
        sweep.append({"tau": tau, **metrics_for(y_true, pred)})

    # 운영점 추천: FNR ≤ target 을 만족하는 행 중 f1_macro 최대
    feasible = [r for r in sweep if r["fnr_underclass"] <= args.fnr_target]
    recommended = max(feasible, key=lambda r: r["f1_macro"]) if feasible else None

    # ── 리포트 ──────────────────────────────────────────────
    def tau_str(t):
        return "argmax" if t is None else f"{t:.2f}"

    md = [
        "# FNR 임계 sweep — 미탐율 ↔ 정밀도 trade 곡선",
        "",
        f"- model_dir: `{args.model_dir}`",
        f"- gold(holdout): `{args.gold}`  (n={len(rows)})",
        f"- 정답 분포: {dict(Counter(y_true))}",
        f"- FNR 목표: ≤ {args.fnr_target:.2f}",
        "",
        "> escalation 규칙: 가장 심각한 등급 g 중 softmax(g) ≥ τ 를 채택, 없으면 argmax 폴백.",
        "> τ를 낮출수록 고등급 승격↑ → **FNR↓** 이지만 **과분류(overclass_s3)↑·검수부하↑**.",
        "",
        "| τ | FNR(미탐) | high→S3 | overclass S3 | 검수부하 | F1 | Acc | R:TS | R:S1 | R:S2 | R:S3 |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for r in sweep:
        mark = "  ⬅ 추천" if (recommended is not None and r is recommended) else ""
        md.append(
            f"| {tau_str(r['tau'])}{mark} | {r['fnr_underclass']:.3f} | {r['high_to_s3']} | "
            f"{r['overclass_s3']:.3f} | {r['review_burden']:.3f} | {r['f1_macro']:.3f} | "
            f"{r['accuracy']:.3f} | {r['recall_TS']:.2f} | {r['recall_S1']:.2f} | "
            f"{r['recall_S2']:.2f} | {r['recall_S3']:.2f} |"
        )

    md += ["", "## 운영점 추천", ""]
    if recommended is not None:
        md += [
            f"- **τ = {tau_str(recommended['tau'])}** 에서 FNR={recommended['fnr_underclass']:.3f} "
            f"(목표 ≤{args.fnr_target:.2f} 충족), F1={recommended['f1_macro']:.3f}, "
            f"과분류(S3)={recommended['overclass_s3']:.3f}, 검수부하={recommended['review_burden']:.3f}",
            "",
            "**대가 해석**: argmax 대비 FNR을 목표까지 낮추는 만큼 진짜 S3 문서가 고등급으로 잘못 올라가",
            "검수 큐 부하가 커진다. 위 표에서 두 비용(overclass_s3·review_burden)을 함께 보고 결정할 것.",
        ]
    else:
        md += [
            f"- **현재 모델로는 임계 조정만으로 FNR ≤ {args.fnr_target:.2f} 도달 불가** "
            "(가장 공격적인 τ에서도 목표 미달).",
            "- 결론: 임계(operating point) 레버는 한계. **비대칭 cost 재학습(②) + 데이터 다양화(③)** 가 필요.",
        ]

    out = Path(args.report)
    if not out.is_absolute():
        out = _HERE.parent / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8")
    out.with_suffix(".json").write_text(
        json.dumps({"model_dir": args.model_dir, "gold": args.gold, "n": len(rows),
                    "fnr_target": args.fnr_target, "sweep": sweep,
                    "recommended_tau": recommended["tau"] if recommended else None},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # stdout 요약
    print(json.dumps({
        "n": len(rows),
        "baseline_argmax": {k: sweep[0][k] for k in ("fnr_underclass", "f1_macro", "overclass_s3", "review_burden")},
        "recommended_tau": recommended["tau"] if recommended else None,
        "recommended": ({k: recommended[k] for k in ("fnr_underclass", "f1_macro", "overclass_s3", "review_burden")}
                        if recommended else None),
        "report": str(out),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
