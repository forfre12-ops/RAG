#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Revalidate the current RF-0001..RF-1001 report source before promotion.

``reports/full1001_20261010/documents`` is the mutable authoring source.
This tool measures the files that are actually present now, rather than
trusting an audit emitted before a later document correction.  Its output can
be supplied to ``prepare_full1001_review_batch.py --audit``; the per-document
digest then makes a later accidental edit fail closed during promotion.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

try:
    from _cli_io import force_utf8_stdio
except ImportError:
    from scripts._cli_io import force_utf8_stdio

from koipa.modules.m1_synthesis.generation_playbook import AVOID_PHRASES
from koipa.services.synth_quality import (
    _contains_pii,
    _exposes_grade_token,
    _exposes_self_grade_declaration,
)


force_utf8_stdio()

POC = Path(__file__).resolve().parents[1]
DEFAULT_REPORT = POC / "reports" / "full1001_20261010"
EXPECTED_IDS = tuple(f"RF-{number:04d}" for number in range(1, 1002))
_TABLE_DIVIDER = re.compile(r"^\s*\|?\s*:?-{3,}:?(?:\s*\|\s*:?-{3,}:?)+\s*\|?\s*$")


def _read_plan(path: Path) -> dict[str, dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read plan: {path}") from exc
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError("plan must be a JSON array of objects")
    rows = {str(row.get("doc_id") or "").strip(): row for row in value}
    if tuple(sorted(rows)) != EXPECTED_IDS or len(rows) != len(value):
        raise ValueError("plan IDs must be exactly RF-0001..RF-1001")
    return rows


def _body_text(text: str, *, doc_id: str) -> str:
    parts = re.split(r"\r?\n\r?\n", text, maxsplit=1)
    if len(parts) != 2 or not parts[0].strip() or not parts[1].strip():
        raise ValueError(f"{doc_id}: expected a title, blank line, and body")
    return parts[1]


def _has_markdown_table(text: str) -> bool:
    lines = [line.strip() for line in text.splitlines()]
    for index, line in enumerate(lines[:-1]):
        # GFM에서는 양 끝의 | 를 생략할 수 있다. 헤더와 구분선이 모두 두 칸 이상인지
        # 확인해, 문장 안의 우연한 | 를 표로 오인하지 않는다.
        header_cells = [cell.strip() for cell in line.strip("|").split("|")]
        if len(header_cells) < 2 or not all(header_cells):
            continue
        if _TABLE_DIVIDER.fullmatch(lines[index + 1]):
            return True
    return False


def audit_report(report: Path) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    plan_path = report / "plan.json"
    documents = report / "documents"
    plan = _read_plan(plan_path)
    rows: list[dict[str, Any]] = []
    failures: list[str] = []

    for doc_id in EXPECTED_IDS:
        item = plan[doc_id]
        try:
            text = (documents / f"{doc_id}.txt").read_text(encoding="utf-8")
            body = _body_text(text, doc_id=doc_id)
        except (OSError, ValueError) as exc:
            failures.append(str(exc))
            continue
        try:
            length_min = int(item["length_min"])
            length_max = int(item["length_max"])
        except (KeyError, TypeError, ValueError) as exc:
            failures.append(f"{doc_id}: invalid planned length range")
            continue
        body_len = len(body)
        table_actual = _has_markdown_table(body)
        avoid_hits = [phrase for phrase in AVOID_PHRASES if phrase in text]
        row = {
            "doc_id": doc_id,
            "department": str(item.get("department") or "").strip(),
            "grade": str(item.get("grade") or "").strip(),
            "doc_format": str(item.get("doc_format") or "").strip(),
            "length_min": length_min,
            "length_max": length_max,
            "body_len": body_len,
            "length_ok": length_min <= body_len <= length_max,
            "include_table": bool(item.get("include_table")),
            "table_actual": table_actual,
            "table_ok": table_actual == bool(item.get("include_table")),
            "avoid_hits": avoid_hits,
            "grade_token": _exposes_grade_token(text),
            "self_decl": _exposes_self_grade_declaration(text),
            "pii": _contains_pii(text),
            "source_file_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
        invalid = [
            name for name, failed in {
                "metadata": not all(row[key] for key in ("department", "grade", "doc_format")),
                "length_ok": not row["length_ok"],
                "table_ok": not row["table_ok"],
                "avoid_hits": bool(row["avoid_hits"]),
                "grade_token": bool(row["grade_token"]),
                "self_decl": bool(row["self_decl"]),
                "pii": bool(row["pii"]),
            }.items() if failed
        ]
        if invalid:
            failures.append(f"{doc_id}: {', '.join(invalid)}")
        rows.append(row)

    if len(rows) != len(EXPECTED_IDS):
        failures.append(f"expected {len(EXPECTED_IDS)} readable documents, found {len(rows)}")
    summary = {
        "source_report": str(report.resolve()),
        "plan_sha256": hashlib.sha256(plan_path.read_bytes()).hexdigest(),
        "document_count": len(rows),
        "failures": failures,
        "passed": not failures,
    }
    return rows, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--out", type=Path, required=True, help="path for the refreshed per-document audit JSON")
    args = parser.parse_args(argv)

    rows, summary = audit_report(args.report.resolve())
    if not summary["passed"]:
        print(json.dumps(summary, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**summary, "audit_results_sha256": hashlib.sha256(args.out.read_bytes()).hexdigest()}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
