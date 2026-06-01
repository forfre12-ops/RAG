"""
P1 오분류 패턴 분석 — gold_real 기준 S2→S3, S1→S3 패턴 파악.

출력:
  reports/misclassification_analysis.json  — 상세 분석
  콘솔: 등급별 오분류 요약 + 상위 패턴 샘플

사용:
  python scripts/analyze_misclassifications.py
  python scripts/analyze_misclassifications.py --gold datasets/gold_real/classification_gold.jsonl
"""
from __future__ import annotations

import argparse
import io
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

if sys.stdout.encoding and sys.stdout.encoding.lower() not in ("utf-8", "utf-8-sig"):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

LABELS = ["TS", "S1", "S2", "S3"]
GRADE_ORDER = {"TS": 1, "S1": 2, "S2": 3, "S3": 4}


def load_gold(path: Path) -> list[dict]:
    recs = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            recs.append(json.loads(line))
    return recs


def run_classifier(recs: list[dict]) -> list[tuple[str, str]]:
    """rule labeler로 전체 gold 분류."""
    from lloydk.modules.m3_labeling import LabelingPipeline
    pipe = LabelingPipeline()
    results = []
    for r in recs:
        out = pipe.label(r["text"])
        pred = out.grade.value if hasattr(out.grade, "value") else str(out.grade)
        results.append((r["label"], pred))
    return results


def analyze(
    recs: list[dict],
    predictions: list[tuple[str, str]],
) -> dict:
    """오분류 패턴 상세 분석."""
    n = len(recs)

    # confusion matrix
    cm: dict[str, dict[str, int]] = {t: {p: 0 for p in LABELS} for t in LABELS}
    for true, pred in predictions:
        if true in cm and pred in cm[true]:
            cm[true][pred] += 1

    # 오분류 케이스 수집
    misclassified: dict[str, list[dict]] = defaultdict(list)
    for rec, (true, pred) in zip(recs, predictions):
        if true != pred:
            pattern = f"{true}→{pred}"
            misclassified[pattern].append({
                "doc_id": rec.get("doc_id", ""),
                "true": true,
                "pred": pred,
                "label_source": rec.get("label_source", ""),
                "domain": rec.get("domain", ""),
                "text_len": len(rec.get("text", "")),
                "text_preview": rec.get("text", "")[:200],
            })

    # 패턴 빈도
    pattern_counts = {k: len(v) for k, v in sorted(misclassified.items(), key=lambda x: -len(x[1]))}

    # 등급별 분석
    per_grade = {}
    for lbl in LABELS:
        true_count = sum(1 for t, _ in predictions if t == lbl)
        correct = cm[lbl][lbl]
        wrong = true_count - correct
        recall = correct / true_count if true_count else 0.0
        # 어디로 오분류됐는가
        misclassed_to = {p: cm[lbl][p] for p in LABELS if p != lbl and cm[lbl][p] > 0}
        per_grade[lbl] = {
            "support": true_count,
            "correct": correct,
            "wrong": wrong,
            "recall": round(recall, 4),
            "misclassified_to": dict(sorted(misclassed_to.items(), key=lambda x: -x[1])),
        }

    # label_source별 오분류율
    source_miss: dict[str, dict] = defaultdict(lambda: {"total": 0, "wrong": 0})
    for rec, (true, pred) in zip(recs, predictions):
        src = rec.get("label_source", "unknown")
        source_miss[src]["total"] += 1
        if true != pred:
            source_miss[src]["wrong"] += 1
    source_miss_rate = {
        k: {"total": v["total"], "miss_rate": round(v["wrong"] / max(v["total"], 1), 4)}
        for k, v in sorted(source_miss.items())
    }

    # 텍스트 길이 vs 오분류
    length_buckets = {"<256": {"total": 0, "wrong": 0}, "256-512": {"total": 0, "wrong": 0},
                      "512-1024": {"total": 0, "wrong": 0}, ">1024": {"total": 0, "wrong": 0}}
    for rec, (true, pred) in zip(recs, predictions):
        l = len(rec.get("text", ""))
        bucket = "<256" if l < 256 else ("256-512" if l < 512 else ("512-1024" if l < 1024 else ">1024"))
        length_buckets[bucket]["total"] += 1
        if true != pred:
            length_buckets[bucket]["wrong"] += 1
    length_analysis = {
        k: {"total": v["total"], "miss_rate": round(v["wrong"] / max(v["total"], 1), 4)}
        for k, v in length_buckets.items()
    }

    return {
        "n": n,
        "accuracy": round(sum(1 for t, p in predictions if t == p) / n, 4),
        "confusion_matrix": cm,
        "per_grade": per_grade,
        "misclassification_patterns": pattern_counts,
        "source_miss_rate": source_miss_rate,
        "text_length_analysis": length_analysis,
        "top_misclassified_samples": {
            k: v[:3] for k, v in list(misclassified.items())[:6]
        },
    }


def print_summary(result: dict) -> None:
    print("\n=== P1 오분류 분석 결과 ===\n")
    print(f"총 {result['n']}건, accuracy={result['accuracy']:.3f}")
    print()

    print("등급별 성능:")
    for lbl in LABELS:
        g = result["per_grade"][lbl]
        miss_to = ", ".join(f"{k}:{v}" for k, v in g["misclassified_to"].items())
        print(f"  {lbl}: recall={g['recall']:.3f}  support={g['support']}  오분류→({miss_to})")
    print()

    print("주요 오분류 패턴:")
    for pattern, cnt in list(result["misclassification_patterns"].items())[:8]:
        total_true = result["per_grade"][pattern.split("→")[0]]["support"]
        pct = cnt / total_true * 100 if total_true else 0
        print(f"  {pattern}: {cnt}건 ({pct:.1f}% of {pattern.split('→')[0]})")
    print()

    print("label_source별 오분류율:")
    for src, v in result["source_miss_rate"].items():
        print(f"  {src}: {v['miss_rate']:.3f} ({v['total']}건)")
    print()

    print("텍스트 길이별 오분류율:")
    for bucket, v in result["text_length_analysis"].items():
        print(f"  {bucket}: miss_rate={v['miss_rate']:.3f} ({v['total']}건)")
    print()

    print("상위 S2→S3 샘플 (최대 2건):")
    for s in result["top_misclassified_samples"].get("S2→S3", [])[:2]:
        print(f"  doc={s['doc_id'][:12]}  len={s['text_len']}  src={s['label_source']}")
        print(f"  text: {s['text_preview'][:120]}...")
    print()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gold", default="datasets/gold_real/classification_gold.jsonl")
    ap.add_argument("--report", default="reports/misclassification_analysis.json")
    args = ap.parse_args()

    gold_path = Path(args.gold)
    if not gold_path.exists():
        print(f"[ERROR] {gold_path} 없음", file=sys.stderr)
        return 2

    print(f"[INFO] gold 로드: {gold_path}")
    recs = load_gold(gold_path)
    print(f"[INFO] {len(recs)}건 분류 중...")

    predictions = run_classifier(recs)
    result = analyze(recs, predictions)

    out = Path(args.report)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print_summary(result)
    print(f"리포트: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
