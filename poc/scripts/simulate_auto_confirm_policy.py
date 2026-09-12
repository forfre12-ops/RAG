"""사람 확정 데이터에서 자동확정 후보 정책을 보수적으로 시뮬레이션한다."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from koipa.services.automation_policy import simulate_policy_grid  # noqa: E402
from koipa.services.automation_report import load_reviewed_records_from_db  # noqa: E402


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="자동확정 후보 정책 시뮬레이터")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path)
    source.add_argument("--from-db", action="store_true")
    parser.add_argument("--thresholds", default="0.70,0.65,0.60,0.55,0.50")
    parser.add_argument("--min-margin", type=float, default=0.0)
    parser.add_argument("--min-evaluated", type=int, default=30)
    parser.add_argument("--model-version", default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    thresholds = [float(value) for value in args.thresholds.split(",") if value.strip()]
    if not thresholds or any(not 0.0 <= value <= 1.0 for value in thresholds):
        parser.error("--thresholds는 0~1 범위의 쉼표 구분 값이어야 합니다.")
    if not 0.0 <= args.min_margin <= 1.0:
        parser.error("--min-margin은 0~1 범위여야 합니다.")
    if args.min_evaluated < 1:
        parser.error("--min-evaluated must be at least 1")
    records = (
        _load_jsonl(args.input)
        if args.input else load_reviewed_records_from_db(args.model_version, args.limit)
    )
    report = simulate_policy_grid(
        records,
        thresholds=thresholds,
        min_margin=args.min_margin,
        min_evaluated=args.min_evaluated,
    )
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
