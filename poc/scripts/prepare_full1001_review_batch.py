#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Promote the audited RF 1,001-document report into the active review batch.

The RF documents are initially produced below ``reports/``; that location is
ignored and is not an input to an offline delivery bundle.  This command makes
the hand-off explicit and reproducible:

1. validate the generation plan, document files, and per-document audit;
2. snapshot the accepted source into ``datasets/full1001_20261010``; and
3. write exactly 1,000 console candidate pairs plus a selection manifest.

It is dry-run by default.  ``--apply`` does not activate the batch; activation
is a separate, auditable call to ``set_active_review_batch.py``.
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
REPORT = POC / "reports" / "full1001_20261010"
CANONICAL_SOURCE = POC / "datasets" / "full1001_20261010"
CANDIDATE_ROOT = POC / "datasets" / "proxy_gold" / "single_document_candidates"
BATCH = "expert_review_product_1000_20261010_rf"
EXPECTED_SOURCE_COUNT = 1001
EXPECTED_REVIEW_COUNT = 1000
REQUIRED_FORMATS = ("응시자 지원서", "고용계약서", "매뉴얼", "각종 계약서")
VALID_GRADES = {"TS", "S1", "S2", "S3"}


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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json_list(path: Path) -> list[dict[str, Any]]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read JSON list: {path}") from exc
    if not isinstance(value, list) or not all(isinstance(row, dict) for row in value):
        raise ValueError(f"expected a JSON array of objects: {path}")
    return value


def _expected_ids() -> list[str]:
    return [f"RF-{number:04d}" for number in range(1, EXPECTED_SOURCE_COUNT + 1)]


def load_validated_source(report: Path, *, audit_path: Path | None = None) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Return auditable source rows only after every RF quality gate passes."""
    plan_path = report / "plan.json"
    audit_path = audit_path or report / "audit_results.json"
    documents = report / "documents"
    plan = _read_json_list(plan_path)
    audit = _read_json_list(audit_path)
    expected_ids = _expected_ids()
    if len(plan) != EXPECTED_SOURCE_COUNT or len(audit) != EXPECTED_SOURCE_COUNT:
        raise ValueError("RF plan and audit must each contain exactly 1,001 rows")

    plan_by_id = {str(row.get("doc_id") or "").strip(): row for row in plan}
    audit_by_id = {str(row.get("doc_id") or "").strip(): row for row in audit}
    if sorted(plan_by_id) != expected_ids or len(plan_by_id) != len(plan):
        raise ValueError("RF plan IDs must be the contiguous RF-0001..RF-1001 sequence")
    if sorted(audit_by_id) != expected_ids or len(audit_by_id) != len(audit):
        raise ValueError("RF audit IDs must match the contiguous RF-0001..RF-1001 sequence")

    rows: list[dict[str, Any]] = []
    for doc_id in expected_ids:
        item = plan_by_id[doc_id]
        result = audit_by_id[doc_id]
        grade = str(item.get("grade") or "").strip()
        department = str(item.get("department") or "").strip()
        doc_format = str(item.get("doc_format") or "").strip()
        if grade not in VALID_GRADES or not department or not doc_format:
            raise ValueError(f"{doc_id}: missing or invalid planning metadata")
        for field, expected in (("grade", grade), ("department", department), ("doc_format", doc_format)):
            if str(result.get(field) or "").strip() != expected:
                raise ValueError(f"{doc_id}: plan/audit {field} differs")
        failures = [
            name for name, failed in {
                "length_ok": not bool(result.get("length_ok")),
                "table_ok": not bool(result.get("table_ok")),
                "avoid_hits": bool(result.get("avoid_hits")),
                "grade_token": bool(result.get("grade_token")),
                "self_decl": bool(result.get("self_decl")),
                "pii": bool(result.get("pii")),
            }.items() if failed
        ]
        if failures:
            raise ValueError(f"{doc_id}: RF quality gate failed: {', '.join(failures)}")
        body_path = documents / f"{doc_id}.txt"
        try:
            text = body_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"{doc_id}: missing document body") from exc
        if not text.strip():
            raise ValueError(f"{doc_id}: blank document body")
        source_file_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
        audited_sha256 = str(result.get("source_file_sha256") or "").strip().lower()
        if audited_sha256 and audited_sha256 != source_file_sha256:
            raise ValueError(f"{doc_id}: audit digest differs from current document body")
        rows.append({
            "doc_id": doc_id,
            "true_grade": grade,
            "department": department,
            "info_type": "합성 검수 문서",
            "doc_format": doc_format,
            "include_table": bool(item.get("include_table")),
            "length_min": item.get("length_min"),
            "length_max": item.get("length_max"),
            "body_len": result.get("body_len"),
            "source_file_sha256": source_file_sha256,
            "text": text,
        })
    missing_formats = [name for name in REQUIRED_FORMATS if not any(row["doc_format"] == name for row in rows[:EXPECTED_REVIEW_COUNT])]
    if missing_formats:
        raise ValueError("selected RF batch is missing required document formats: " + ", ".join(missing_formats))
    provenance = {
        "source_report": str(report.resolve()),
        "plan_sha256": _sha256(plan_path),
        "audit_results_path": str(audit_path.resolve()),
        "audit_results_sha256": _sha256(audit_path),
        "source_document_count": len(rows),
        "quality_gate": {
            "length_ok": True,
            "table_ok": True,
            "avoid_hits": 0,
            "grade_token": 0,
            "self_decl": 0,
            "pii": 0,
        },
    }
    return rows, provenance


def select_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if len(rows) != EXPECTED_SOURCE_COUNT:
        raise ValueError(f"RF source must contain exactly {EXPECTED_SOURCE_COUNT} rows")
    ordered = sorted(rows, key=lambda row: str(row["doc_id"]))
    return ordered[:EXPECTED_REVIEW_COUNT], {
        "selection_rule": "stable ascending RF doc_id prefix; independent of grade, content, and audit result",
        "selected_doc_ids": [row["doc_id"] for row in ordered[:EXPECTED_REVIEW_COUNT]],
        "excluded_doc_ids": [row["doc_id"] for row in ordered[EXPECTED_REVIEW_COUNT:]],
    }


def stage_canonical_source(rows: list[dict[str, Any]], provenance: dict[str, Any], destination: Path) -> Path:
    if destination.exists():
        raise ValueError(f"canonical RF source already exists; refusing to overwrite: {destination}")
    manifest_rows = [{key: value for key, value in row.items() if key != "text"} for row in rows]
    for row in rows:
        _atomic_write(destination / "documents" / f"{row['doc_id']}.txt", row["text"])
    manifest_path = destination / "full1001_manifest.jsonl"
    _atomic_write(manifest_path, "".join(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n" for row in manifest_rows))
    source_manifest = {
        "schema_version": "full1001-source-v1",
        **provenance,
        "canonical_manifest_sha256": _sha256(manifest_path),
        "documents": len(rows),
    }
    _atomic_write(destination / "SOURCE_MANIFEST.json", json.dumps(source_manifest, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(destination / "README.md", "# RF 전문가 검수 원본 1,001건\n\n"
                  "`reports/full1001_20261010`의 계획·전수 감사 통과본을 고정한 원본이다. "
                  "검수 배치에는 안정 `doc_id` 정렬 기준 RF-0001..RF-1000만 사용하며 RF-1001은 제외한다.\n")
    return manifest_path


def _candidate_metadata(row: dict[str, Any]) -> dict[str, Any]:
    text = str(row["text"])
    return {
        "doc_id": row["doc_id"],
        "intended_label": row["true_grade"],
        "document_origin": "synthetic",
        "title": text.split("\n", 1)[0].strip(),
        "document_type": f"모의문서({row['department']}/{row['doc_format']})",
        "department": row["department"],
        "info_type": row["info_type"],
        "doc_format": row["doc_format"],
        "authoring_method": "full1001_generation_playbook_20261010",
        "requires_manual_audit": True,
        "candidate_status": "proposed",
        "source_file_sha256": row["source_file_sha256"],
        "source_reference": "AI 합성 RF 1,001건. plan.json 및 audit_results.json 전수 품질 게이트 통과본.",
        "claim_scope": "검수 전 후보. 전문가 확정 전 등급은 잠정값이며 실제 비밀정보가 아닌 가상 내용이다.",
        "import_note": "full1001_20261010 원본에서 RF doc_id 안정 정렬로 1,000건을 선별한 전문가 검수 배치.",
        "review_batch": BATCH,
        "review_batch_active": False,
        "review_batch_note": "20명 x 50건 전문가 검수용 RF 1,000건. RF-1001은 등급·본문·품질과 무관하게 안정 ID 규칙으로 제외.",
    }


def write_candidates(selected: list[dict[str, Any]], selection: dict[str, Any], *, root: Path, source_manifest: Path,
                     provenance: dict[str, Any]) -> None:
    collisions = [row["doc_id"] for row in selected if (root / f"{row['doc_id']}.metadata.json").exists()]
    if collisions:
        raise ValueError("RF candidates already exist; refusing to overwrite: " + ", ".join(collisions[:5]))
    selection_path = root / f"{BATCH}.selection.json"
    if selection_path.exists():
        raise ValueError(f"selection manifest already exists; refusing to overwrite: {selection_path}")
    for row in selected:
        _atomic_write(root / f"{row['doc_id']}_review.md", str(row["text"]))
        _atomic_write(root / f"{row['doc_id']}.metadata.json", json.dumps(_candidate_metadata(row), ensure_ascii=False, indent=2) + "\n")
    record = {
        "schema_version": "expert-review-batch-selection-v2",
        "review_batch": BATCH,
        "source_manifest": str(source_manifest.resolve()),
        "source_manifest_sha256": _sha256(source_manifest),
        "source_document_count": len(selected) + len(selection["excluded_doc_ids"]),
        "review_document_count": len(selected),
        "quality_gate": provenance["quality_gate"],
        **selection,
    }
    _atomic_write(selection_path, json.dumps(record, ensure_ascii=False, indent=2) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report", type=Path, default=REPORT)
    parser.add_argument("--audit", type=Path, help="refreshed per-document audit JSON; enforces source file digests when present")
    parser.add_argument("--canonical-source", type=Path, default=CANONICAL_SOURCE)
    parser.add_argument("--root", type=Path, default=CANDIDATE_ROOT)
    parser.add_argument("--apply", action="store_true", help="stage source and write RF console candidates")
    args = parser.parse_args(argv)
    audit_path = args.audit.resolve() if args.audit else None
    rows, provenance = load_validated_source(args.report.resolve(), audit_path=audit_path)
    selected, selection = select_rows(rows)
    print(json.dumps({"review_batch": BATCH, **provenance, "review_document_count": len(selected), **selection}, ensure_ascii=False, indent=2))
    if not args.apply:
        print("--apply not supplied: no files changed")
        return 0
    manifest_path = stage_canonical_source(rows, provenance, args.canonical_source.resolve())
    write_candidates(selected, selection, root=args.root.resolve(), source_manifest=manifest_path, provenance=provenance)
    print(f"prepared {len(selected)} RF candidates for {BATCH}; activate with set_active_review_batch.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
