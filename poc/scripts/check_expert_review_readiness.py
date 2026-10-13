#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Verify that an expert-review deployment is safe to open.

This is deliberately read-only.  It checks the *loaded console ledger*, not a
nearby delivery folder, so a locally generated 1,000-document package cannot
be mistaken for the batch actually available to reviewers.

Example (run in the deployment container or with its mounted data):

    python scripts/check_expert_review_readiness.py \
      --expected-documents 1000 --expected-reviewers 20 \
      --require-enforcement \
      --require-doc-format "응시자 지원서" \
      --require-doc-format "고용계약서" \
      --require-doc-format "매뉴얼" \
      --require-doc-format "각종 계약서"

The command never prints passwords or password hashes.  A non-zero exit means
that the reviewer console must not be opened yet.
"""
from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

try:
    from _cli_io import force_utf8_stdio
except ImportError:  # package import in tests/release checks
    from scripts._cli_io import force_utf8_stdio


force_utf8_stdio()


POC_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CANDIDATE_ROOT = POC_ROOT / "datasets" / "proxy_gold" / "single_document_candidates"
DEFAULT_CREDENTIALS = POC_ROOT / "datasets" / "proxy_gold" / "reviewer_credentials.json"
VALID_GRADES = {"TS", "S1", "S2", "S3"}
TRUE_VALUES = {"1", "true", "yes", "on"}


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"{path.name}: unreadable JSON ({type(exc).__name__})") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{path.name}: JSON root must be an object")
    return value


def _normalise(value: object) -> str:
    return str(value or "").strip()


def _candidate_grade(row: dict[str, Any]) -> str:
    """Read the grade field used by both historical and current importers."""
    for key in ("intended_label", "proposed_grade", "true_grade", "grade"):
        grade = _normalise(row.get(key)).upper()
        if grade:
            return grade
    return ""


def _reviewer_account_count(credentials_path: Path) -> int:
    payload = _read_json(credentials_path)
    count = 0
    for record in payload.values():
        if not isinstance(record, dict):
            continue
        roles = {_normalise(role) for role in record.get("roles", [])}
        if "reviewer" in roles:
            count += 1
    return count


def evaluate(
    *,
    candidate_root: Path,
    credentials_path: Path,
    expected_documents: int,
    expected_reviewers: int,
    required_formats: list[str],
    require_enforcement: bool,
    environ: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Return a redacted readiness report.  This function makes no writes."""
    env = os.environ if environ is None else environ
    errors: list[str] = []
    rows: list[dict[str, Any]] = []

    if not candidate_root.is_dir():
        errors.append(f"candidate root does not exist: {candidate_root}")
    else:
        for path in sorted(candidate_root.glob("*.metadata.json")):
            try:
                rows.append(_read_json(path))
            except ValueError as exc:
                errors.append(str(exc))

    active_rows = [row for row in rows if bool(row.get("review_batch_active"))]
    active_batches = Counter(
        _normalise(row.get("review_batch")) or "<unnamed>" for row in active_rows
    )
    if not active_rows:
        errors.append("no active review batch in the loaded candidate ledger")
    if len(active_batches) != 1:
        errors.append(f"exactly one active review batch is required (found {len(active_batches)})")

    active_count = len(active_rows)
    if active_count != expected_documents:
        errors.append(
            f"active review batch has {active_count} documents; expected {expected_documents}"
        )

    grades = Counter(_candidate_grade(row) or "<missing>" for row in active_rows)
    bad_grades = sorted(grade for grade in grades if grade not in VALID_GRADES)
    if bad_grades:
        errors.append("active batch has missing/invalid provisional grades: " + ", ".join(bad_grades))

    formats = Counter(_normalise(row.get("doc_format")) or "<missing>" for row in active_rows)
    missing_formats = [name for name in required_formats if formats.get(name, 0) == 0]
    if missing_formats:
        errors.append("required document formats missing: " + ", ".join(missing_formats))

    reviewer_count = 0
    if not credentials_path.is_file():
        errors.append(f"reviewer credential store does not exist: {credentials_path}")
    else:
        try:
            reviewer_count = _reviewer_account_count(credentials_path)
        except ValueError as exc:
            errors.append(str(exc))
    if reviewer_count != expected_reviewers:
        errors.append(
            f"reviewer account count is {reviewer_count}; expected {expected_reviewers}"
        )

    enforcement = {
        "assignment": _normalise(env.get("GOLDEN_REVIEWER_ASSIGNMENT_ENFORCED")).lower() in TRUE_VALUES,
        "blind": _normalise(env.get("GOLDEN_REVIEW_BLIND_ENFORCED")).lower() in TRUE_VALUES,
    }
    if require_enforcement:
        missing = [name for name, enabled in enforcement.items() if not enabled]
        if missing:
            errors.append("required reviewer controls disabled: " + ", ".join(missing))

    return {
        "ready": not errors,
        "candidate_root": str(candidate_root),
        "active_batches": dict(sorted(active_batches.items())),
        "active_document_count": active_count,
        "expected_document_count": expected_documents,
        "provisional_grade_distribution": dict(sorted(grades.items())),
        "document_format_distribution": dict(sorted(formats.items())),
        "reviewer_account_count": reviewer_count,
        "expected_reviewer_count": expected_reviewers,
        "reviewer_controls": enforcement,
        "errors": errors,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--candidate-root", type=Path, default=DEFAULT_CANDIDATE_ROOT)
    parser.add_argument("--credentials", type=Path, default=DEFAULT_CREDENTIALS)
    parser.add_argument("--expected-documents", type=int, default=1000)
    parser.add_argument("--expected-reviewers", type=int, default=20)
    parser.add_argument("--require-doc-format", action="append", default=[])
    parser.add_argument("--require-enforcement", action="store_true")
    parser.add_argument("--json", action="store_true", help="print machine-readable output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.expected_documents < 1 or args.expected_reviewers < 1:
        raise SystemExit("expected document and reviewer counts must be positive")
    report = evaluate(
        candidate_root=args.candidate_root.resolve(),
        credentials_path=args.credentials.resolve(),
        expected_documents=args.expected_documents,
        expected_reviewers=args.expected_reviewers,
        required_formats=[item.strip() for item in args.require_doc_format if item.strip()],
        require_enforcement=args.require_enforcement,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print("READY" if report["ready"] else "NOT READY")
        print("active batches:", report["active_batches"])
        print("documents:", f"{report['active_document_count']}/{report['expected_document_count']}")
        print("reviewer accounts:", f"{report['reviewer_account_count']}/{report['expected_reviewer_count']}")
        print("reviewer controls:", report["reviewer_controls"])
        for error in report["errors"]:
            print("-", error)
    return 0 if report["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
