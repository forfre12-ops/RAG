"""모델 온도(T)를 **난이도 있는 독립 셋**에서 다시 적합한다.

왜 필요한가(실측 2026-08-12). v6 학습 후 나온 `temperature.json` 이 **T = 0.05** 였다.
정상 범위는 2~3 이다(미보정 서빙은 OOD 과신으로 고등급 무음 미탐 위험이라 보정이 필수).
0.05 가 나온 이유는 간단하다 — v6 의 검증셋 정확도가 **100%** 라 보정할 신호가 없었다.
완벽한 셋에서는 온도를 추정할 수 없고, 경계값이 그대로 나온다.

argmax 판정에는 T 가 영향을 주지 않지만(단조 변환) **confidence 를 쓰는 게이트에는
그대로 영향이 있다** — escalation tau · 저신뢰 검수 라우팅 · 합의 게이트의 conf 임계.
따라서 finalize 전에 반드시 다시 잡아야 한다.

보정셋은 **v3 development_200** 을 쓴다:
  · 학습셋과 문장 풀이 다르다(eval_fact_pools ↔ v6_fact_pools, 공유 0종)
  · 난이도가 있다(v6 val 은 100% 라 신호가 없다)
  · **봉인된 final_800 이 아니다** — 봉인을 보정에 쓰면 그 셋으로 잰 수치가 오염된다
  · dev200 의 tell 커버 0.185 는 여기서 문제가 안 된다. 온도를 맞추는 것이지
    정확도를 주장하는 것이 아니다

사용:
    python scripts/recalibrate_temperature.py \
        --model-dir artifacts_out/classifier_v6_baseline/v-8cef4fd2 \
        --calibration datasets/proxy_eval/direct_authored_proxy_eval_split.v3/development_200.jsonl
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))


def _read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="문서 단위 온도 재적합")
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--calibration", required=True)
    parser.add_argument("--out", default=None, help="기본: <model-dir>/temperature.json")
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--device", choices=["auto", "cpu", "cuda"], default="auto")
    parser.add_argument("--write", action="store_true",
                        help="없으면 계산만 하고 파일을 쓰지 않는다")
    args = parser.parse_args(argv)

    from koipa.proxy_training_finalization import (  # noqa: PLC0415
        fit_document_temperature,
        load_model_document_logits,
    )

    rows = _read_jsonl(Path(args.calibration))
    model_dir = Path(args.model_dir)
    print(f"[calib] {args.calibration} · {len(rows)}건", flush=True)

    batch = load_model_document_logits(
        model_dir, rows, batch_size=args.batch_size,
        device=args.device, require_fast_overflow=True,
    )
    report = fit_document_temperature(batch.documents)

    old = None
    existing = model_dir / "temperature.json"
    if existing.is_file():
        old = json.loads(existing.read_text("utf-8")).get("temperature")

    report["source"] = "recalibrated-on-independent-set"
    report["calibration_set"] = args.calibration
    report["previous_temperature"] = old
    report["note"] = (
        "v6 학습 직후 값은 T=0.05 였다(검증셋 정확도 100% 라 보정 신호 없음). "
        "문장 풀이 다른 독립 셋에서 다시 적합한 값이다. argmax 는 T 에 불변이지만 "
        "confidence 기반 게이트는 영향을 받는다."
    )
    print(json.dumps({k: v for k, v in report.items() if k != "note"},
                     ensure_ascii=True, indent=2))
    print(f"\n[T] 이전 {old} -> 신규 {report['temperature']}")

    out = Path(args.out) if args.out else existing
    if args.write:
        out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                       encoding="utf-8")
        print(f"[written] {out}")
    else:
        print("(--write 없음: 파일 미변경)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
