"""배포 모델을 임의의 평가셋에 **서빙 경로 그대로** 태워 채점한다.

왜 필요한가. `compare_proxy_models.py` 는 baseline↔candidate A/B 용이라 모델 두 개를
요구한다. 그런데 지금 필요한 것은 **모델 하나를 새 자로 재는 것**이다 —
2026-08-12 에 학습셋(v6)과 평가셋(v3) 양쪽의 지름길(길이·정답문장)을 제거했고,
현행 배포본이 그 정직한 자에서 몇 점인지가 이후 모든 비교의 기준선이 된다.

채점 경로는 M5 서빙과 동일하다(`load_model_document_logits`):
문자 청크 → fast tokenizer overflow 윈도 → 온도 적용 → 길이가중 평균 → 심각도 max →
재정규화 → argmax. 원시 모델 argmax 가 아니다 — 실제 운영이 내는 값이어야 의미가 있다.

⚠ 이 스크립트는 **번들을 만들지 않는다.** finalize 가 아니므로 소스 계보 가드
(`require_clean_source_tree`)를 걸지 않는다. 산출물은 측정 기록일 뿐이고, 배포 경로에
올라가는 것은 finalize 를 거친 번들뿐이다.

사용:
    python scripts/score_model_on_eval.py \
        --model-dir artifacts/classifier_p1_v5_clean/v-fe4b386b \
        --eval datasets/proxy_eval/direct_authored_proxy_eval_split.v3/final_800.locked.jsonl \
        --out reports/score_v5_on_v3_final800.json
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

GRADES = ("TS", "S1", "S2", "S3")
# 고등급 = 미탐이 문제가 되는 등급. 본 사업 1차 목표가 이쪽이다.
HIGH = ("TS", "S1")


def _read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _f1_macro(truth: list[str], pred: list[str]) -> tuple[float, dict]:
    per: dict[str, dict] = {}
    for grade in GRADES:
        tp = sum(1 for t, p in zip(truth, pred) if t == grade and p == grade)
        fp = sum(1 for t, p in zip(truth, pred) if t != grade and p == grade)
        fn = sum(1 for t, p in zip(truth, pred) if t == grade and p != grade)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        per[grade] = {
            "n": sum(1 for t in truth if t == grade),
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
        }
    return sum(v["f1"] for v in per.values()) / len(GRADES), per


def _underclass_fnr(truth: list[str], pred: list[str]) -> dict:
    """고등급을 더 낮은 등급으로 본 비율 — 본 사업이 가장 경계하는 오류."""
    order = {g: i for i, g in enumerate(GRADES)}  # TS=0 이 가장 높다
    out = {}
    for grade in HIGH:
        rows = [(t, p) for t, p in zip(truth, pred) if t == grade]
        missed = sum(1 for t, p in rows if order[p] > order[t])
        out[grade] = {
            "n": len(rows),
            "underclassified": missed,
            "fnr": round(missed / len(rows), 4) if rows else 0.0,
        }
    total = sum(v["n"] for v in out.values())
    missed = sum(v["underclassified"] for v in out.values())
    out["high_combined"] = {
        "n": total,
        "underclassified": missed,
        "fnr": round(missed / total, 4) if total else 0.0,
    }
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Score one model on one eval set through the M5 serving path"
    )
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--eval", required=True)
    parser.add_argument("--out", default=None)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--limit", type=int, default=0, help="앞 N건만(빠른 확인용). 0=전체")
    args = parser.parse_args(argv)

    from lloydk.proxy_training_finalization import load_model_document_logits  # noqa: PLC0415
    from lloydk.proxy_training_finalization import aggregate_trace_probabilities  # noqa: PLC0415

    rows = _read_jsonl(Path(args.eval))
    if args.limit:
        rows = rows[: args.limit]
    print(f"[eval] {args.eval} · {len(rows)}건", flush=True)

    model_dir = Path(args.model_dir)
    temperature_path = model_dir / "temperature.json"
    temperature = 1.0
    if temperature_path.is_file():
        temperature = float(json.loads(temperature_path.read_text("utf-8"))["temperature"])
    print(f"[model] {model_dir} · T={temperature}", flush=True)

    batch = load_model_document_logits(
        model_dir, rows, batch_size=args.batch_size,
        device=args.device, require_fast_overflow=True,
    )
    truth, pred = [], []
    for trace in batch.documents:
        probabilities = aggregate_trace_probabilities(trace, temperature=temperature)
        truth.append(str(trace.label))
        pred.append(max(probabilities, key=probabilities.get))

    f1, per_grade = _f1_macro(truth, pred)
    accuracy = sum(1 for t, p in zip(truth, pred) if t == p) / len(truth)
    report = {
        "model_dir": str(model_dir),
        "eval_set": args.eval,
        "documents": len(truth),
        "temperature": temperature,
        "scoring_path": (
            "M5 aggregation only: char chunk -> fast-tokenizer overflow windows -> T -> "
            "length-weighted mean -> severe max -> renormalize -> argmax. "
            "POST-MODEL SERVING GUARDS ARE NOT APPLIED (FNR-safe override, source-prior cap, "
            "metadata floor, escalation tau, agreement gate) — these are exactly the "
            "excluded_post_model_serving_rules of the aggregation contract. 운영 FNR 은 이보다 낮다."
        ),
        "accuracy": round(accuracy, 4),
        "f1_macro": round(f1, 4),
        "per_grade": per_grade,
        "underclassification_fnr": _underclass_fnr(truth, pred),
        "predicted_distribution": dict(sorted(Counter(pred).items())),
        "truth_distribution": dict(sorted(Counter(truth).items())),
        "claim_ceiling": (
            "합성 내부 일관성까지. 실문서 일반화 근거가 아니다. 이 수치를 다른 평가셋의 "
            "수치와 나란히 놓지 말 것 — 자가 다르면 비교가 성립하지 않는다."
        ),
    }
    print(json.dumps({k: v for k, v in report.items() if k != "claim_ceiling"},
                     ensure_ascii=False, indent=2))
    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        print(f"[report] {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
