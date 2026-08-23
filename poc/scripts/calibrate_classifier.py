"""P2-A6: 분류기 confidence calibration — Temperature scaling.

학습된 분류기의 softmax confidence는 종종 over-confident.
val set에 대해 temperature T를 최적화하여 ECE(Expected Calibration Error) 감소.

사용:
  python scripts/calibrate_classifier.py \
      --val datasets/labeled/val.jsonl \
      --model-dir artifacts/classifier \
      --output artifacts/classifier/temperature.json

운영시:
  classifier.predict(text) 결과의 logits에 1/T 를 곱해 softmax 재산정.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

# [중복 제거] 온도 스케일링 핵심은 m6_evaluation.temperature 로 일원화 — trainer 자동연결과 공유.
from koipa.modules.m6_evaluation.temperature import (  # noqa: E402
    expected_calibration_error,
    find_best_temperature,
    neg_log_likelihood,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="A6: temperature scaling calibration")
    parser.add_argument("--val", type=Path, required=False, help="val.jsonl (없으면 dummy)")
    parser.add_argument("--model-dir", type=Path, default=Path("artifacts/classifier"))
    # 기본 출력은 model-dir/temperature.json — 서빙(m5 pipeline)이 model_dir에서만 T를
    # 읽으므로, --output을 생략하면 보정 결과가 서빙이 읽는 곳에 정확히 떨어진다.
    # (과거 고정 default 'artifacts/classifier/temperature.json'은 --model-dir을 다르게
    #  주면 죽은 경로에 기록돼 보정이 서빙에 반영되지 않는 사고가 있었다.)
    parser.add_argument("--output", type=Path, default=None,
                        help="기본: <model-dir>/temperature.json")
    parser.add_argument("--n-bins", type=int, default=10)
    parser.add_argument("--allow-dummy", action="store_true",
                        help="실제 val logits가 없을 때 더미로 진행(검증 전용). 미지정 시 fail-loud.")
    args = parser.parse_args()
    if args.output is None:
        args.output = args.model_dir / "temperature.json"

    # [보정 배선] --val 미지정 시 모델 dir에 동봉된 val_logits.jsonl 자동 탐색.
    # trainer.py가 학습 종료 시 검증셋 per-row logits를 <model-dir>/val_logits.jsonl로
    # 남긴다 → --val 없이도 *실제* logits로 보정해 더미/배포금지 분기를 우회한다.
    # 사용자가 --val을 명시하면 그게 최우선(기존 동작 보존).
    if not args.val:
        _auto = args.model_dir / "val_logits.jsonl"
        if _auto.exists():
            args.val = _auto
            print(f"[INFO] val 미지정 — 모델 동봉 {_auto} 사용(실 logits 보정).", file=sys.stderr)

    # 실제 학습된 모델이 없으면 dummy logits로 dry 실행
    if args.val and args.val.exists():
        logits_list: list[list[float]] = []
        labels: list[int] = []
        with args.val.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                row = json.loads(line)
                # 학습 시 출력했던 logits 필드 가정 (없으면 skip)
                lg = row.get("logits")
                yl = row.get("label_idx") if "label_idx" in row else row.get("label")
                if lg is not None and yl is not None:
                    logits_list.append(list(map(float, lg)))
                    labels.append(int(yl) if isinstance(yl, int) else 0)
        if not logits_list:
            print("[WARN] val.jsonl에 logits·label_idx 없음 — dummy로 진행", file=sys.stderr)
            args.val = None

    if not args.val or not args.val.exists():
        # #10: 더미 logits로 적합한 T는 의미 없다 — 실수로 배포되지 않게 fail-loud.
        # 검증·CI 목적이면 --allow-dummy를 명시해야 진행한다.
        if not args.allow_dummy:
            print(
                "[ERR] 실제 val logits 없음 — 더미 logits로 만든 temperature는 배포 금지.\n"
                "      --val <logits·label_idx 포함 jsonl>를 제공하거나, 검증 목적이면 "
                "--allow-dummy를 명시하세요.",
                file=sys.stderr,
            )
            return 1
        print("[WARN] --allow-dummy: 더미 logits로 진행 — 산출 T는 배포 금지(검증 전용).", file=sys.stderr)
        # dummy: over-confident logits
        logits_list = [
            [3.0, 1.0, 0.5, 0.2], [2.5, 1.2, 0.7, 0.1], [3.5, 0.8, 0.3, 0.1],
            [1.0, 3.0, 0.5, 0.2], [0.8, 2.5, 1.0, 0.3], [0.5, 3.2, 1.1, 0.2],
            [0.5, 0.8, 3.0, 1.0], [0.3, 1.0, 2.8, 0.9], [0.5, 0.9, 3.5, 1.1],
            [0.2, 0.3, 0.5, 3.0], [0.1, 0.4, 0.8, 2.7], [0.3, 0.5, 0.7, 3.5],
        ]
        labels = [0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3]

    T_star = find_best_temperature(logits_list, labels)
    ece_before = expected_calibration_error(logits_list, labels, T=1.0, n_bins=args.n_bins)
    ece_after = expected_calibration_error(logits_list, labels, T=T_star, n_bins=args.n_bins)
    nll_before = neg_log_likelihood(logits_list, labels, 1.0)
    nll_after = neg_log_likelihood(logits_list, labels, T_star)

    report = {
        "temperature": round(T_star, 4),
        "ece_before": round(ece_before, 4),
        "ece_after": round(ece_after, 4),
        "nll_before": round(nll_before, 4),
        "nll_after": round(nll_after, 4),
        "n_samples": len(labels),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
