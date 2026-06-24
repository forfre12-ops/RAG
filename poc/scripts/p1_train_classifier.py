"""P1 PoC — 분류 모델 학습 + Confusion Matrix + FNR 측정.

두 가지 모드:
  --mode full   : KF-DeBERTa/KoELECTRA 실제 학습 (GPU 권장)
  --mode dryrun : 룰 라벨러를 분류기 surrogate로 사용한 평가 드라이런

3층 평가 체계 (보고 시 반드시 구분):

  ① synthetic_masked
      --eval-type synthetic_masked (기본)
      파일: datasets/gold/classification_gold.jsonl
      합성 파이프라인 내부 일관성 확인용. 운영 근거 불가.

  ② llm_judge_gold
      --eval-type llm_judge_gold
      파일: datasets/gold_real/classification_gold.jsonl
      --label-source-filter llm_judge_primary,llm_judge_consensus,codex_review
      LLM pseudo-gold 기준. 운영 참고용.

  ③ human_review_gold
      --eval-type human_review_gold
      파일: datasets/gold_real/classification_gold.jsonl
      --label-source-filter human_review
      human_review=0이면 N/A 리포트 출력 (FAIL 아님).

합격선 (doc/02 §3.3):
  - F1-macro ≥ 0.75
  - FNR (특급→하위 미탐) ≤ 5%

사용:
  python scripts/p1_train_classifier.py --mode dryrun
  python scripts/p1_train_classifier.py --mode dryrun --eval-type llm_judge_gold \\
      --label-source-filter llm_judge_primary,llm_judge_consensus,codex_review
  python scripts/p1_train_classifier.py --mode dryrun --eval-type human_review_gold \\
      --label-source-filter human_review
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

# eval_type → 기본 gold 파일 경로
_EVAL_TYPE_PATHS = {
    "synthetic_masked":  Path("datasets/gold/classification_gold.jsonl"),
    "llm_judge_gold":    Path("datasets/gold_real/classification_gold.jsonl"),
    "human_review_gold": Path("datasets/gold_real/classification_gold.jsonl"),
}

# eval_type → 기본 label_source 필터 (--label-source-filter 미지정 시)
_EVAL_TYPE_DEFAULT_FILTER: dict[str, list[str] | None] = {
    "synthetic_masked":  None,
    "llm_judge_gold":    ["llm_judge_primary", "llm_judge_consensus", "codex_review"],
    "human_review_gold": ["human_review"],
}


def load_jsonl(path: Path, label_source_filter: list[str] | None = None) -> list[dict]:
    """JSONL 로드. label_source_filter 지정 시 해당 레코드만 반환."""
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        r = json.loads(line)
        if label_source_filter is not None:
            if r.get("label_source") not in label_source_filter:
                continue
        rows.append(r)
    return rows


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
    metrics = compute_metrics(y_true, y_pred)
    # dryrun 예측기는 m3 키워드 규칙 라벨러이지 학습된 v3 모델이 아니다.
    # 운영 모델 성능은 eval_p1_model_gold.py(m5_inference / 트랜스포머) 참조.
    metrics["predictor"] = "m3_labeling.rule_engine (keyword seeds; NOT the trained v3 model)"
    return metrics


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
    """리포트 작성. n=0이면 N/A 리포트(PASS/FAIL 미결정)."""
    eval_type = metrics.get("eval_type", "synthetic_masked")
    lsf = metrics.get("label_source_filter")
    filter_note = f" [filter: {','.join(lsf)}]" if lsf else ""

    # 주의: --mode dryrun 예측기는 m3 규칙 라벨러이며 학습된 v3 모델이 아니다.
    # 따라서 이 리포트의 F1/판정은 '운영 모델 성능'이 아니라 '규칙 라벨러 성능'이다.
    # 운영 모델 성능 근거는 eval_p1_model_gold.py(m5_inference 트랜스포머) 리포트.
    eval_notes = {
        "synthetic_masked":  "⚠ synthetic_masked — 합성 파이프라인 연결 확인용. 운영 근거 불가.",
        "llm_judge_gold":    "~ llm_judge_gold — LLM pseudo-gold 대상, 규칙 라벨러 예측. 운영 참고용.",
        "human_review_gold": "⚠ human_review_gold — 규칙 라벨러(m3) 예측이며 학습 모델 아님. 운영 모델 성능은 eval_p1_model_gold 참조.",
    }
    eval_note = eval_notes.get(eval_type, eval_type)
    predictor = metrics.get("predictor")

    # n=0 → N/A (human_review_gold 미구축 등)
    if metrics.get("n", 0) == 0:
        verdict = "N/A"
        md = [
            f"# P1 — 분류 모델 평가 리포트 ({mode})",
            "",
            f"- **eval_type**: `{eval_type}`{filter_note} — {eval_note}",
            f"- **판정**: N/A — 해당 label_source 데이터가 없습니다.",
            "",
            "human_review 데이터를 gold_real/classification_gold.jsonl에 추가한 후 재실행하세요.",
        ]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(md), encoding="utf-8")
        na_metrics = {**metrics, "verdict": "N/A"}
        out.with_suffix(".json").write_text(
            json.dumps(na_metrics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return verdict

    f1_pass = metrics["f1_macro"] >= 0.75
    fnr_pass = metrics["fnr_underclass"] <= 0.05
    verdict = "PASS" if (f1_pass and fnr_pass) else "FAIL"

    md = [
        f"# P1 — 분류 모델 평가 리포트 ({mode})",
        "",
        f"- **eval_type**: `{eval_type}`{filter_note} — {eval_note}",
        f"- **predictor**: `{predictor}`" if predictor else "",
        f"- **판정**: {verdict} (예측기 기준 — 위 predictor 주의)",
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
    out.with_suffix(".json").write_text(
        json.dumps({**metrics, "verdict": verdict}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return verdict


def main() -> int:
    ap = argparse.ArgumentParser(description="P1 분류기 학습/평가")
    ap.add_argument("--mode", choices=["dryrun", "full"], default="dryrun")
    ap.add_argument(
        "--eval-type",
        choices=["synthetic_masked", "llm_judge_gold", "human_review_gold"],
        default="synthetic_masked",
        help=(
            "synthetic_masked  : gold/classification_gold.jsonl (기본, 운영 근거 불가)\n"
            "llm_judge_gold    : gold_real/ (LLM pseudo-gold, 참고용)\n"
            "human_review_gold : gold_real/ (사람 검수 only, 미구축이면 N/A)"
        ),
    )
    ap.add_argument("--gold", default=None,
                    help="gold JSONL 파일 직접 지정 (--eval-type 기본 경로 오버라이드)")
    ap.add_argument("--label-source-filter", default=None,
                    help="쉼표 구분 label_source 필터 (예: human_review / llm_judge_primary,codex_review). "
                         "미지정 시 --eval-type 기본 필터 적용.")
    ap.add_argument("--test", default=None, help="(하위 호환) gold JSONL 직접 지정")
    ap.add_argument("--report", default="reports/p1_classifier_report.md")
    ap.add_argument("--epochs", type=int, default=5)
    ap.add_argument("--seed", type=int, default=None,
                    help="학습 시드(모델 init·데이터 셔플). 미지정 시 TrainSpec 기본 42.")
    ap.add_argument("--train-path", default=None)
    ap.add_argument("--val-path", default=None)
    ap.add_argument("--test-path", default=None)
    ap.add_argument("--batch-size", type=int, default=None)
    ap.add_argument("--base-model", default=None)
    ap.add_argument("--output-dir", default=None)
    ap.add_argument("--fnr-cost-multiplier", type=float, default=None,
                    help="고등급(TS/S1) 손실 가중 배수. >1이면 미탐 비용↑ → FNR↓ (권장 2.0~3.0)")
    ap.add_argument("--early-stop-metric", default=None,
                    help="best 모델 선택 지표 (기본 fnr_high). fnr_overall 등으로 오버라이드 가능")
    ap.add_argument("--no-mlflow", action="store_true",
                    help="MLflow 로깅 비활성화 (서버 없는 환경 또는 속도 우선)")
    ap.add_argument("--no-bf16", action="store_true", help="bf16 비활성화")
    ap.add_argument("--max-seq-len", type=int, default=None,
                    help="최대 시퀀스 길이 (기본 512. 256으로 줄이면 4× 빨라짐)")
    args = ap.parse_args()

    eval_type = args.eval_type

    # label_source 필터 결정: 명시 > eval_type 기본값
    if args.label_source_filter:
        lsf = [s.strip() for s in args.label_source_filter.split(",") if s.strip()]
    else:
        lsf = _EVAL_TYPE_DEFAULT_FILTER[eval_type]

    # gold 파일 경로 결정: --gold > --test(하위호환) > eval_type 기본값
    gold_path_override = args.gold or args.test
    if gold_path_override:
        gold_path = Path(gold_path_override)
    else:
        gold_path = _EVAL_TYPE_PATHS[eval_type]

    if args.mode == "full":
        from lloydk.modules.m4_training.trainer import TrainSpec, train_classifier

        spec_kwargs: dict = {"epochs": args.epochs}
        for k, v in [("train_path", args.train_path), ("val_path", args.val_path),
                     ("test_path", args.test_path), ("batch_size", args.batch_size),
                     ("base_model", args.base_model), ("output_dir", args.output_dir),
                     ("fnr_cost_multiplier", args.fnr_cost_multiplier),
                     ("early_stop_metric", args.early_stop_metric),
                     ("seed", args.seed)]:
            if v is not None:
                spec_kwargs[k] = v
        if getattr(args, "no_mlflow", False):
            spec_kwargs["use_mlflow"] = False
        if getattr(args, "no_bf16", False):
            spec_kwargs["bf16"] = False
        if getattr(args, "max_seq_len", None):
            spec_kwargs["max_seq_len"] = args.max_seq_len
        spec = TrainSpec(**spec_kwargs)
        print(f"[p1] full mode spec: {spec_kwargs}", file=sys.stderr)
        report = train_classifier(spec)
        print(json.dumps(report.__dict__, ensure_ascii=False, indent=2, default=str))
        return 0

    # dryrun: gold 파일 로드
    if not gold_path.exists():
        if eval_type == "human_review_gold":
            # 미구축이면 N/A로 처리
            print(f"[p1] human_review_gold: {gold_path} 없음 → N/A 리포트 작성", file=sys.stderr)
            metrics = {"n": 0, "eval_type": eval_type, "label_source_filter": lsf}
            write_report(metrics, args.mode, Path(args.report))
            print(f"\n[p1] verdict: N/A -> {args.report}")
            return 0
        if eval_type == "llm_judge_gold":
            print(f"[p1] llm_judge_gold: {gold_path} 없음. make llm-judge 먼저 실행하세요.", file=sys.stderr)
            return 2
        # synthetic_masked fallback
        synth_dir = Path("datasets/synthetic")
        if not synth_dir.exists() or not list(synth_dir.glob("*.json")):
            print("[p1] no test data — run make_gold_set.py or pass --gold", file=sys.stderr)
            return 2
        rows = build_test_from_synth(synth_dir)
        print("[p1] WARNING: using raw synthetic test data (no gold set found)", file=sys.stderr)
        lsf = None
    else:
        rows = load_jsonl(gold_path, label_source_filter=lsf)
        filter_info = f" [filter: {','.join(lsf)}]" if lsf else ""
        print(
            f"[p1] eval_type={eval_type}{filter_info}, path={gold_path} ({len(rows)} docs)",
            file=sys.stderr,
        )

    # n=0 처리: human_review_gold 미구축 등
    if len(rows) == 0:
        print(f"[p1] 해당 label_source 데이터 없음 (filter={lsf}) → N/A 리포트", file=sys.stderr)
        metrics = {"n": 0, "eval_type": eval_type, "label_source_filter": lsf}
        write_report(metrics, args.mode, Path(args.report))
        print(f"\n[p1] verdict: N/A -> {args.report}")
        return 0

    metrics = evaluate_dryrun(rows)
    metrics["eval_type"] = eval_type
    if lsf:
        metrics["label_source_filter"] = lsf
    verdict = write_report(metrics, args.mode, Path(args.report))
    print(json.dumps(
        {k: v for k, v in metrics.items() if k != "confusion_matrix"},
        ensure_ascii=False, indent=2,
    ))
    print(f"\n[p1] verdict: {verdict} -> {args.report}")
    return 0 if verdict in ("PASS", "N/A") else 1


if __name__ == "__main__":
    raise SystemExit(main())
