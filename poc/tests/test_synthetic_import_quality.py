"""Regression tests for the read-only synthetic import quality audit."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from koipa.services.synthetic_import_quality import (
    AUDIT_SCHEMA_VERSION,
    audit_records,
    discover_input_files,
    load_metadata_index,
    load_records,
    merge_metadata,
    normalize_record,
    report_checksum,
)


def _record(
    *,
    doc_id: str = "doc-1",
    grade: str = "S1",
    document_type: str = "업무 매뉴얼",
    title: str = "운영 절차 검토",
    body: str = "운영 절차의 점검 결과와 후속 조치를 기록한다.",
) -> dict:
    return {
        "synth_id": doc_id,
        "target_grade": grade,
        "document_type": document_type,
        "title": title,
        "body": body,
    }


def _audit(rows: list[dict]) -> dict:
    records = [normalize_record(row, source_path="fixture.json", source_index=i) for i, row in enumerate(rows)]
    return audit_records(records)


def _decision(report: dict, doc_id: str) -> dict:
    return next(row for row in report["decisions"] if row["document_id"] == doc_id)


def test_clean_records_are_kept_and_distribution_is_reported():
    rows = [
        _record(doc_id="a", grade="TS", document_type="계약 검토서", body="계약 조건의 검토 결과를 기록한다."),
        _record(doc_id="b", grade="S1", document_type="업무 매뉴얼", body="작업 절차의 변경 이력과 검토 결과를 기록한다."),
    ]
    report = _audit(rows)

    assert report["schema_version"] == AUDIT_SCHEMA_VERSION
    assert report["summary"]["keep"] == 2
    assert report["distribution"]["by_grade"] == {"S1": 1, "TS": 1}
    assert report["distribution"]["by_document_type_grade"]["계약 검토서"] == {"TS": 1}


def test_direct_content_violations_are_replace_candidates():
    report = _audit([
        _record(doc_id="label", body="이 문서는 1급 비밀 자료이며 외부 공유 금지다."),
        _record(doc_id="person", body="김민수 부장이 검토 결과를 기록했다."),
        _record(doc_id="echo", grade="S1", body="팀장급만 확인한 핵심 공정 노하우를 정리했다."),
    ])

    assert _decision(report, "label")["disposition"] == "replace"
    assert {i["code"] for i in _decision(report, "label")["issues"]} >= {"grade_token_exposed", "self_grade_declaration"}
    assert {i["code"] for i in _decision(report, "person")["issues"]} == {"korean_name_and_role"}
    assert _decision(report, "echo")["issues"] == [{"code": "prompt_echo", "severity": "replace", "detail": "팀장급, 핵심 공정 노하우"}]


def test_korean_department_role_is_not_misread_as_a_person_name():
    report = _audit([_record(doc_id="dept", body="생산본부장이 검토 결과를 기록했다. 구매팀 대리도 참석했다.")])
    assert _decision(report, "dept")["disposition"] == "keep"


def test_missing_required_source_metadata_is_excluded():
    report = _audit([_record(doc_id="bad", grade="", document_type="", body="")])
    decision = _decision(report, "bad")
    assert decision["disposition"] == "exclude"
    assert {issue["code"] for issue in decision["issues"]} == {
        "missing_body", "invalid_or_missing_grade", "missing_document_type",
    }


def test_duplicate_and_repeated_template_are_replace_candidates_but_first_copy_remains():
    shared = (
        "동일한 문서 본문을 반복하지 않기 위한 예시 문장입니다. 충분히 긴 문단을 넣어 템플릿 검사도 함께 확인합니다. "
        "문서마다 고유한 사실과 후속 조치를 넣어야 하며 이 문장은 반복 탐지 전용입니다."
    )
    rows = [
        _record(doc_id="first", grade="TS", body=shared),
        _record(doc_id="duplicate", grade="S1", body=shared),
        _record(doc_id="third", grade="S2", body=shared + "\n\n다른 결론을 추가한다."),
    ]
    report = _audit(rows)
    assert _decision(report, "first")["disposition"] == "replace"  # template repetition still applies
    assert "duplicate_document" in {x["code"] for x in _decision(report, "duplicate")["issues"]}
    assert report["issue_counts"]["repeated_template_paragraph"] == 3


def test_structure_concentration_is_batch_blocker_not_silent_per_row_replacement():
    rows = [
        _record(
            doc_id=f"n-{i}", grade=("TS", "S1", "S2")[i],
            body="1. 검토 범위\n내용\n2. 확인 결과\n내용\n3. 후속 조치\n내용",
        )
        for i in range(3)
    ]
    report = _audit(rows)
    assert report["release_blockers"][0]["code"] == "structure_distribution_concentrated"
    assert report["summary"]["release_ready"] is False


def test_load_json_jsonl_and_cli_report_manifest_are_read_only(tmp_path: Path):
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    json_path = input_dir / "one.json"
    json_path.write_text(json.dumps([_record(doc_id="json")], ensure_ascii=False), encoding="utf-8")
    jsonl_path = input_dir / "two.jsonl"
    jsonl_path.write_text(json.dumps(_record(doc_id="jsonl"), ensure_ascii=False) + "\n", encoding="utf-8")
    before = {path: path.read_bytes() for path in (json_path, jsonl_path)}

    files = discover_input_files(input_dir)
    records, errors = load_records(files)
    assert len(records) == 2 and errors == []

    report_path = tmp_path / "out" / "report.json"
    manifest_path = tmp_path / "out" / "manifest.json"
    script = Path(__file__).resolve().parents[2] / "scripts" / "audit_synthetic_documents.py"
    completed = subprocess.run(
        [sys.executable, str(script), str(input_dir), "--report", str(report_path), "--manifest", str(manifest_path)],
        check=True, capture_output=True, text=True,
    )
    assert completed.stdout == ""
    assert report_path.exists() and manifest_path.exists()
    assert {path: path.read_bytes() for path in (json_path, jsonl_path)} == before
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert manifest["documents"] == 2
    assert len(manifest["input_files"]) == 2
    assert manifest["report_sha256"] == report_checksum(report) == report["report_sha256"]


def test_blind_reviewer_file_can_be_joined_to_internal_metadata_without_writing_labels(tmp_path: Path):
    reviewer = tmp_path / "reviewer_documents.jsonl"
    reviewer.write_text(json.dumps({"review_id": "MK-001", "text": "검토 결과를 기록한다."}, ensure_ascii=False), encoding="utf-8")
    internal = tmp_path / "internal_manifest.jsonl"
    internal.write_text(json.dumps({"review_id": "MK-001", "true_grade": "S2", "doc_format": "계약 검토서"}, ensure_ascii=False), encoding="utf-8")

    records, errors = load_records([reviewer])
    metadata, metadata_errors = load_metadata_index(internal)
    merged = merge_metadata(records, metadata)
    report = audit_records(merged)

    assert errors == metadata_errors == []
    assert report["summary"]["keep"] == 1
    decision = report["decisions"][0]
    assert decision["grade"] == "S2" and decision["document_type"] == "계약 검토서"
    assert "S2" not in reviewer.read_text(encoding="utf-8")
