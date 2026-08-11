"""학습셋 ↔ 홀드아웃 계보 독립성 + 누출 계량 보고서.

모델 비교를 **하기 전에** 돌린다. 여기서 usable_for_comparison=false 가 나오면 그 비교로
나온 수치는 인용할 수 없다 — 같은 생성기가 만든 셋에서 F1 0.99 를 받고 실문서에서 0.61 로
무너진 사례가 이미 있다(2026-08-09 v4_3).

사용:
    python scripts/report_holdout_independence.py \
        --train datasets/proxy_gold/direct_authored_catalog_training.v6.jsonl \
        --holdout datasets/proxy_eval/direct_authored_proxy_eval_split.v2_2/development_200.jsonl \
        [--out reports/holdout_independence.json] [--strict]

``--strict`` 는 usable_for_comparison=false 일 때 exit 1 — CI·파이프라인 배선용.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT / "src"))

from lloydk.holdout_independence import assess  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--train", required=True)
    parser.add_argument("--holdout", required=True)
    parser.add_argument("--label", default="holdout")
    parser.add_argument("--out", default=None, help="JSON 보고서 경로(미지정 시 표준출력만)")
    parser.add_argument(
        "--strict", action="store_true",
        help="비교에 쓸 수 없는 상태면 exit 1 — 파이프라인이 조용히 지나가지 않게",
    )
    args = parser.parse_args(argv)

    report = assess(
        _read_jsonl(Path(args.train)),
        _read_jsonl(Path(args.holdout)),
        label=args.label,
    )

    holdout = report["holdout_leakage"]
    print(f"[independence] 계보 독립: {report['lineage_independent']}")
    print(
        "[leakage] 홀드아웃 길이-only {:.3f} · Theil's U {:.3f} · tell 커버 {:.3f}".format(
            holdout.get("length_only_1nn", 0.0),
            holdout.get("length_theils_u", 0.0),
            holdout.get("tell_coverage", 0.0),
        )
    )
    for concern in report["concerns"]:
        print(f"  ! {concern}")
    print(f"[verdict] 비교 사용 가능: {report['usable_for_comparison']}")
    print(f"[claim]   {report['claim_ceiling']}")

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        print(f"[report] {out}")

    if args.strict and not report["usable_for_comparison"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
