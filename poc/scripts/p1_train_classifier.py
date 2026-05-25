"""P1 PoC — 분류 모델 학습 + Confusion Matrix + FNR 측정.

두 가지 모드:
  --mode full   : KF-DeBERTa/KoELECTRA 실제 학습 (GPU 권장)
  --mode dryrun : 룰 라벨러를 분류기 surrogate로 사용한 평가 드라이런

합격선 (doc/02 §3.3):
  - F1-macro ≥ 0.75
  - FNR (특급→하위 미탐) ≤ 5%

사용:
  python scripts/p1_train_classifier.py --mode dryrun
  python scripts/p1_train_classifier.py --mode full --epochs 5
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

LABELS = ["TS", "S1", "S2", "S3"]
GRADE_ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}


def load_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def build_test_from_synth(synth_dir: Path) -> list[dict]:
    rows: list[dict] = []
    for f in sorted(synth_dir.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        rows.append({"text": f"{d['title']}\n\n{d['body']}", "label": d["target_grade"]})
    return rows


def evaluate_dryrun(rows: list[dict]) -> dict:
    from lloydk.modules.m3_labeling import LabelingPipeline

    pipe = LabelingPipeline()
    y_true: list[str] = []
    y_pred: list[str] = []
    for r in rows:
        out = pipe.label(r["text"])
        pred = out.grade.value if hasattr(out.grade, "value") else str(out.grade)
        y_true.append(r["label"])
        y_pred.append(pred)
    return compute_metrics(y_true, y_pred)


def compute_metrics(y_true: list[str], y_pred: list[str]) -> dict:
    n = len(y_true)
    correct = sum(1 for a, b in zip(y_true, y_pred) if a == b)
    accuracy = correct / n if n else 0.0

    per_class: dict[str, dict[str, float]] = {}
    for lbl in LABELS:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == lbl and p == lbl)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != lbl and p == lbl)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == lbl and p != lbl)
        prec = tp / (tp + fp) if (tp + fp) else 0.0
        rec = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * prec * rec / (prec + rec) if (prec + rec) else 0.0
        per_class[lbl] = {"precision": prec, "recall": rec, "f1": f1, "support": tp + fn}
    f1_macro = sum(c["f1"] for c in per_class.values()) / len(LABELS)

    cm = {t: {p: 0 for p in LABELS} for t in LABELS}
    for t, p in zip(y_true, y_pred):
        if t in cm and p in cm[t]:
            cm[t][p] += 1

    high_total = sum(1 for t in y_true if GRADE_ORDER[t] <= 2)
    high_under = sum(
        1 for t, p in zip(y_true, y_pred)
        if GRADE_ORDER[t] <= 2 and GRADE_ORDER[p] > GRADE_ORDER[t]
    )
    fnr_under = high_under / max(high_total, 1)
    fnr_by = {lbl: 1.0 - per_class[lbl]["recall"] for lbl in LABELS}

    return {
        "n": n,
        "accuracy": round(accuracy, 4),
        "f1_macro": round(f1_macro, 4),
        "fnr_underclass": round(fnr_under, 4),
        "fnr_by_grade": {k: round(v, 4) for k, v in fnr_by.items()},
        "per_class": {
            k: {kk: round(vv, 4) if isinstance(vv, float) else vv for kk, vv in v.items()}
            for k, v in per_class.items()
        },
        "confusion_matrix": cm,
    }


def write_report(metrics: dict, mode: str, out: Path) -> str:
    f1_pass = metrics["f1_macro"] >= 0.75
    fnr_pass = metrics["fnr_underclass"] <= 0.05
    verdict = "PASS" if (f1_pass and fnr_pass) else "FAIL"
    md = [
        f"# P1 — 분류 모델 평가 리포트 ({mode})",
        "",
        f"- **판정**: {verdict}",
        f"- F1-macro ≥ 0.75: {metrics['f1_macro']:.3f} ({'PASS' if f1_pass else 'FAIL'})",
        f"- FNR(특급→하위 미탐) ≤ 5%: {metrics['fnr_underclass']*100:.2f}% ({'PASS' if fnr_pass else 'FAIL'})",
        f"- Accuracy: {metrics['accuracy']:.3f} (n={metrics['n']})",
        "",
        "## 등급별",
        "",
        "| Grade | P | R | F1 | N |",
        "|---|---:|---:|---:|---:|",
    ]
    for g in LABELS:
        c = metrics["per_class"][g]
        md.append(f"| {g} | {c['precision']:.3f} | {c['recall']:.3f} | {c['f1']:.3f} | {c['support']} |")
    md += [
        "",
        "## Confusion Matrix (truth ↓ / pred →)",
        "",
        "| | " + " | ".join(LABELS) + " |",
        "|" + "---|" * (len(LABELS) + 1),
    ]
    for t in LABELS:
        md.append("| " + t + " | " + " | ".join(str(metrics["confusion_matrix"][t][p]) for p in LABELS) + " |")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return verdict


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["dryrun", "full"], default="dryrun")
    ap.add_argument("--test", default=None, help="test.jsonl (없으면 datasets/synthetic 사용)")
    ap.add_argument("--report", default="reports/p1_classifier_report.md")
    ap.add_argument("--epochs", type=int, default=5)
    args = ap.parse_args()

    if args.test:
        rows = load_jsonl(Path(args.test))
    else:
        synth_dir = Path("datasets/synthetic")
        if not synth_dir.exists() or not list(synth_dir.glob("*.json")):
            print("[p1] no test data — run p3_generate_synthetic.py first or pass --test", file=sys.stderr)
            return 2
        rows = build_test_from_synth(synth_dir)

    if args.mode == "full":
        from lloydk.modules.m4_training.trainer import TrainSpec, train_classifier

        spec = TrainSpec(epochs=args.epochs)
        report = train_classifier(spec)
        print(json.dumps(report.__dict__, ensure_ascii=False, indent=2, default=str))
        return 0

    metrics = evaluate_dryrun(rows)
    verdict = write_report(metrics, args.mode, Path(args.report))
    print(json.dumps({k: v for k, v in metrics.items() if k != "confusion_matrix"}, ensure_ascii=False, indent=2))
    print(f"\n[p1] verdict: {verdict} -> {args.report}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
