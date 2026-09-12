"""Validate high-fidelity proxy corpus candidates before training or review.

Example:
    python scripts/validate_proxy_corpus.py \
      --input datasets/proxy_gold/candidates.jsonl \
      --report reports/proxy_gold_validation.json \
      --valid-out datasets/proxy_gold/eligible_candidates.jsonl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_POC = _HERE.parent
sys.path.insert(0, str(_POC / "src"))

from koipa.proxy_corpus import validate_proxy_corpus, validate_proxy_record  # noqa: E402


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SystemExit(f"invalid JSONL at {path}:{line_no}: {exc.msg}") from exc
        if not isinstance(row, dict):
            raise SystemExit(f"record must be an object at {path}:{line_no}")
        rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows),
        encoding="utf-8",
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate proxy-corpus provenance and quality gates")
    parser.add_argument("--input", required=True, help="candidate JSONL")
    parser.add_argument("--report", help="write validation report JSON")
    parser.add_argument("--valid-out", help="write only eligible records as JSONL")
    parser.add_argument("--stage", choices=("candidate", "eligible"), default="eligible")
    parser.add_argument(
        "--intended-use",
        choices=("training", "evaluation"),
        default="training",
        help="permission contract to enforce for public-real records",
    )
    args = parser.parse_args(argv)

    rows = _read_jsonl(Path(args.input))
    report = validate_proxy_corpus(
        rows, stage=args.stage, intended_use=args.intended_use
    )
    if args.report:
        target = Path(args.report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if args.valid_out:
        _write_jsonl(
            Path(args.valid_out),
            [
                row
                for row in rows
                if validate_proxy_record(
                    row,
                    stage=args.stage,
                    intended_use=args.intended_use,
                ).ok
            ],
        )

    print(json.dumps({key: report[key] for key in ("total", "valid", "invalid", "error_counts")}, ensure_ascii=False))
    return 0 if report["invalid"] == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
