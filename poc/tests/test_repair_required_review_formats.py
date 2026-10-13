from __future__ import annotations

from scripts.repair_required_review_formats import (
    MIN_FORMAT_DOCUMENT_CHARS,
    _matches_required_signature,
    repaired_text,
)


def _row(doc_format: str) -> dict[str, str]:
    return {
        "doc_id": "MS1-0001",
        "true_grade": "S2",
        "department": "인사",
        "info_type": "운영 정보",
        "doc_format": doc_format,
    }


def test_repair_templates_have_the_required_format_signatures_without_grade_terms():
    for doc_format in ("응시자 지원서", "고용계약서", "각종 계약서"):
        text = repaired_text(_row(doc_format))
        assert text is not None
        assert _matches_required_signature(text, doc_format)
        assert len(text) >= MIN_FORMAT_DOCUMENT_CHARS
        assert "S2" not in text


def test_repair_does_not_replace_unrelated_document_formats():
    assert repaired_text(_row("매뉴얼")) is None
