"""그림자 자동확정 관측치와 사람 확정 결과를 연결해 운영점을 평가한다.

사용 예시:
  python scripts/analyze_auto_confirm_shadow.py --from-db --out reports/automation_shadow.json
  python scripts/analyze_auto_confirm_shadow.py --input reviewed_export.jsonl

DB 경로는 ``confirmed``/``corrected`` 상태만 사용한다. 같은 등급으로 확정된 건은
예측값을 정답으로 보고, 정정이 있으면 가장 최근 corrected_level을 정답으로 사용한다.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from koipa.services.automation_report import (  # noqa: E402
    build_automation_report,
    load_reviewed_records_from_db,
)


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="자동확정 그림자 정책 리포트")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--input", type=Path, help="사람 확정 결과를 포함한 JSONL export")
    source.add_argument("--from-db", action="store_true", help="운영 DB의 최종 확정 건을 조회")
    parser.add_argument("--model-version", default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--out", type=Path, default=None)
    args = parser.parse_args(argv)

    records = (
        _load_jsonl(args.input)
        if args.input else load_reviewed_records_from_db(args.model_version, args.limit)
    )
    report = build_automation_report(records)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True)
    print(rendered)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(rendered + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
