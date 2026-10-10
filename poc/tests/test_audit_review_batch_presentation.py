from __future__ import annotations

import json
from pathlib import Path

from scripts.audit_review_batch_presentation import audit
from scripts.repair_required_review_formats import repaired_text


def _row(doc_id: str, grade: str, doc_format: str) -> dict[str, str]:
    return {
        "doc_id": doc_id,
        "true_grade": grade,
        "department": "인사",
        "info_type": "운영 정보",
        "doc_format": doc_format,
    }


def _write(root: Path, row: dict[str, str], text: str) -> None:
    doc_id = row["doc_id"]
    (root / f"{doc_id}.metadata.json").write_text(json.dumps({
        "doc_id": doc_id,
        "review_batch": "test-batch",
        "intended_label": row["true_grade"],
        "doc_format": row["doc_format"],
        "title": f"{row['doc_format']} {doc_id}",
    }, ensure_ascii=False), encoding="utf-8")
    (root / f"{doc_id}_review.md").write_text(text, encoding="utf-8")


def _manual() -> str:
    return (
        "운영 매뉴얼\n적용범위: 검수 운영 전반\n\n"
        "1. 절차: 담당자는 접수 순서와 확인 항목을 기록한다.\n"
        "2. 단계: 이상 징후가 있으면 소관 부서에 전달한다.\n"
        "3. 준수사항: 업무 자료를 승인된 범위에서만 열람한다.\n"
        + "운영 기준을 확인한다. " * 55
    )


def _complete_batch(root: Path) -> None:
    formats = ["응시자 지원서", "고용계약서", "매뉴얼", "각종 계약서"]
    grades = ["TS", "S1", "S2", "S3"]
    for index, (doc_format, grade) in enumerate(zip(formats, grades), 1):
        row = _row(f"DOC-{index}", grade, doc_format)
        text = _manual() if doc_format == "매뉴얼" else repaired_text(row)
        assert text is not None
        _write(root, row, text)


def test_required_formats_are_a_release_gate_without_imposing_tables_or_every_grade_per_type(tmp_path):
    _complete_batch(tmp_path)

    report = audit(tmp_path, "test-batch", expected_count=4, min_types=4, min_chars=700)

    assert report["release_ready"] is True
    assert report["checks"]["required_format_characteristics"] is True
    assert report["observed"]["table_documents"] == 0


def test_required_format_with_a_label_only_is_rejected(tmp_path):
    _complete_batch(tmp_path)
    (tmp_path / "DOC-2_review.md").write_text("채용 검토 메모\n" + "일반 안내. " * 100, encoding="utf-8")

    report = audit(tmp_path, "test-batch", expected_count=4, min_types=4, min_chars=700)

    assert report["release_ready"] is False
    assert "고용계약서" in report["failures"]["required_format_characteristics"]
