#!/usr/bin/env python
"""Compare four classification evidence paths without changing serving decisions.

Usage:
    python scripts/run_four_path_shadow.py INPUT.jsonl --out reports/four_path.jsonl
    python scripts/run_four_path_shadow.py INPUT.jsonl --policy policy.json --out reports/four_path.jsonl

Input rows need ``doc_id`` and ``text`` (or ``content``).  ``metadata`` is
optional.  A policy run additionally needs per-row ``policy_packet`` and
``policy_context`` values compatible with ``koipa.policy_shadow``.  The runner
does not invent facts, enable regulation/LLM flags, or modify classifications.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

POC = Path(__file__).resolve().parents[1]
if str(POC / "src") not in sys.path:
    sys.path.insert(0, str(POC / "src"))

from koipa.four_path_shadow import (  # noqa: E402
    compare_row,
    load_jsonl,
    summarize,
    write_jsonl,
)
from koipa.policy_shadow import parse_shadow_policy  # noqa: E402


def _policy(path: Path | None):
    if path is None:
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read policy JSON: {path}") from exc
    return parse_shadow_policy(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="JSONL rows with doc_id and text/content")
    parser.add_argument("--out", type=Path, required=True, help="new JSONL evidence output (never overwritten)")
    parser.add_argument("--summary-out", type=Path, help="new JSON summary sidecar; default is <out>.summary.json")
    parser.add_argument("--policy", type=Path, help="optional versioned policy JSON for shadow-only proposals")
    parser.add_argument("--limit", type=int, default=0, help="maximum input rows (0 means all)")
    args = parser.parse_args(argv)
    if args.limit < 0:
        parser.error("--limit must be zero or positive")
    if args.out.exists():
        parser.error(f"--out already exists: {args.out}")
    summary_out = args.summary_out or args.out.with_name(args.out.name + ".summary.json")
    if summary_out.exists():
        parser.error(f"--summary-out already exists: {summary_out}")

    try:
        rows = load_jsonl(args.input)
        policy = _policy(args.policy)
    except ValueError as exc:
        parser.error(str(exc))
    if args.limit:
        rows = rows[:args.limit]

    # Import only after input/policy validation.  It avoids constructing a DB or
    # model service when the user supplied an invalid report request.
    from koipa.services.classify_service import ClassifyService  # noqa: PLC0415

    service = ClassifyService()
    records = [compare_row(row, line_number=index, service=service, policy=policy)
               for index, row in enumerate(rows, start=1)]
    try:
        write_jsonl(args.out, records)
        summary_out.parent.mkdir(parents=True, exist_ok=True)
        summary_out.write_text(json.dumps(summarize(records), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except (FileExistsError, OSError) as exc:
        parser.error(str(exc))
    print(f"four-path shadow complete: {len(records)} records -> {args.out}")
    print(f"summary -> {summary_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
