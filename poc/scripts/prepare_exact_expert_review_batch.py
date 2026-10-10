#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Create the exact 1,000-document expert-review batch from the approved 1,001 source.

This does not regenerate or edit the source corpus.  It derives a reviewer
batch deterministically: sort the source ``doc_id`` values and retain the
first 1,000.  The final source record is kept in the console pool as an
unbatched candidate, so it cannot accidentally enter the 20 reviewers x 50
documents plan.

The command is dry-run by default.  ``--apply`` writes the console-facing
metadata/body pairs; run ``set_active_review_batch.py`` afterwards to make the
new batch the only active reviewer batch.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any

try:
    from _cli_io import force_utf8_stdio
except ImportError:
    from scripts._cli_io import force_utf8_stdio


force_utf8_stdio()

POC = Path(__file__).resolve().parents[1]
SOURCE = POC / "datasets" / "mock1000_scaleup_20261007"
CANDIDATE_ROOT = POC / "datasets" / "proxy_gold" / "single_document_candidates"
BATCH = "expert_review_product_1000_20261010"
EXPECTED_SOURCE_COUNT = 1001
EXPECTED_REVIEW_COUNT = 1000
REQUIRED_FORMATS = ("응시자 지원서", "고용계약서", "매뉴얼", "각종 계약서")


def _read_manifest(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError(f"manifest line {number} is not JSON") from exc
        if not isinstance(row, dict):
            raise ValueError(f"manifest line {number} is not an object")
        rows.append(row)
    return rows


def select_rows(rows: list[dict[str, Any]], *, expected_source_count: int = EXPECTED_SOURCE_COUNT,
                expected_review_count: int = EXPECTED_REVIEW_COUNT) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return the stable 1,000-record prefix and a provenance record.

    The strict source-count requirement prevents a later source regeneration
    from silently changing who is excluded.  Selection never depends on
    grade, body text, or quality/audit outcome.
    """
    if len(rows) != expected_source_count:
        raise ValueError(f"source manifest must contain exactly {expected_source_count} rows (got {len(rows)})")
    if expected_source_count != expected_review_count + 1:
        raise ValueError("this selector requires exactly one source record more than the review batch")

    by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        doc_id = str(row.get("doc_id") or "").strip()
        if not doc_id:
            raise ValueError("source manifest contains a blank doc_id")
        if doc_id in by_id:
            raise ValueError(f"source manifest contains duplicate doc_id: {doc_id}")
        for field in ("true_grade", "department", "info_type", "doc_format"):
            if not str(row.get(field) or "").strip():
                raise ValueError(f"{doc_id}: required field {field} is blank")
        by_id[doc_id] = row

    sorted_ids = sorted(by_id)
    selected_ids = sorted_ids[:expected_review_count]
    excluded_ids = sorted_ids[expected_review_count:]
    missing_formats = [
        doc_format for doc_format in REQUIRED_FORMATS
        if not any(str(by_id[doc_id].get("doc_format") or "").strip() == doc_format for doc_id in selected_ids)
    ]
    if missing_formats:
        raise ValueError("selected batch is missing required document formats: " + ", ".join(missing_formats))

    return [by_id[doc_id] for doc_id in selected_ids], {
        "selection_rule": "stable ascending doc_id prefix; independent of grade, content, and audit result",
        "selected_doc_ids": selected_ids,
        "excluded_doc_ids": excluded_ids,
    }


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", newline="\n", dir=path.parent,
        prefix=f".{path.name}.", suffix=".tmp", delete=False,
    ) as handle:
        handle.write(text)
        temporary = Path(handle.name)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _source_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metadata(row: dict[str, Any], text: str) -> dict[str, Any]:
    doc_id = str(row["doc_id"])
    return {
        "doc_id": doc_id,
        "intended_label": row["true_grade"],
        "document_origin": "synthetic",
        "title": text.split("\n", 1)[0].strip(),
        "document_type": f"모의문서({row['department']}/{row['info_type']}, {row['doc_format']})",
        "department": row["department"],
        "info_type": row["info_type"],
        "doc_format": row["doc_format"],
        "authoring_method": "claude_subagent_playbook_scaleup_20261007",
        "requires_manual_audit": True,
        "candidate_status": "proposed",
        "source_reference": "AI 합성 문서. 사람 전문가 검수 전 등급은 잠정값이다.",
        "claim_scope": "검수 전 후보. 전문가 확정 전까지 등급은 잠정값이며 실 비밀정보 아님(전부 가상 내용).",
        "import_note": "1,001건 생성본에서 doc_id 안정 정렬로 1,000건을 선별한 전문가 검수 배치.",
        "review_batch": BATCH,
        "review_batch_active": False,
        "review_batch_note": (
            "20명 x 50건 전문가 검수용 1,000건. source 1,001건 중 마지막 doc_id 1건만 제외; "
            "등급·본문·품질과 무관한 선별."
        ),
    }


def _unbatch_excluded(root: Path, excluded_doc_id: str) -> None:
    """Keep the extra source document auditable but outside every review batch."""
    metadata_path = root / f"{excluded_doc_id}.metadata.json"
    if not metadata_path.is_file():
        return
    payload = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or str(payload.get("doc_id") or "").strip() != excluded_doc_id:
        raise ValueError(f"existing excluded candidate metadata is invalid: {metadata_path.name}")
    payload["review_batch"] = ""
    payload["review_batch_active"] = False
    payload["review_exclusion_reason"] = (
        "Not in the exact 1,000-document expert-review batch; stable doc_id selection excluded this one source record."
    )
    _atomic_write(metadata_path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--root", type=Path, default=CANDIDATE_ROOT)
    parser.add_argument("--apply", action="store_true", help="write the selected batch to the console candidate root")
    args = parser.parse_args(argv)

    source = args.source.resolve()
    manifest_path = source / "scaleup_manifest.jsonl"
    document_root = source / "documents"
    rows = _read_manifest(manifest_path)
    selected, selection = select_rows(rows)
    selected_ids = [str(row["doc_id"]) for row in selected]
    missing_bodies = [doc_id for doc_id in selected_ids if not (document_root / f"{doc_id}.txt").is_file()]
    if missing_bodies:
        raise ValueError("selected source documents are missing: " + ", ".join(missing_bodies[:20]))

    record = {
        "schema_version": "expert-review-batch-selection-v1",
        "review_batch": BATCH,
        "source_manifest": str(manifest_path),
        "source_manifest_sha256": _source_sha256(manifest_path),
        "source_document_count": len(rows),
        "review_document_count": len(selected),
        **selection,
    }
    print(json.dumps(record, ensure_ascii=False, indent=2))
    if not args.apply:
        print("--apply not supplied: no files changed")
        return 0

    root = args.root.resolve()
    for row in selected:
        doc_id = str(row["doc_id"])
        text = (document_root / f"{doc_id}.txt").read_text(encoding="utf-8")
        _atomic_write(root / f"{doc_id}_review.md", text)
        _atomic_write(root / f"{doc_id}.metadata.json", json.dumps(_metadata(row, text), ensure_ascii=False, indent=2) + "\n")
    _unbatch_excluded(root, str(selection["excluded_doc_ids"][0]))
    _atomic_write(root / f"{BATCH}.selection.json", json.dumps(record, ensure_ascii=False, indent=2) + "\n")
    print(f"prepared {len(selected)} candidates for {BATCH}; activate it with set_active_review_batch.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
