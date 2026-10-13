from __future__ import annotations

import pytest

from scripts.prepare_exact_expert_review_batch import REQUIRED_FORMATS, select_rows


def _row(number: int, *, doc_format: str = "보고서") -> dict[str, str]:
    return {
        "doc_id": f"MS1-{number:04d}",
        "true_grade": "S2",
        "department": "기획",
        "info_type": "사업 정보",
        "doc_format": doc_format,
    }


def _source_rows() -> list[dict[str, str]]:
    rows = [_row(number) for number in range(1, 1002)]
    for number, doc_format in enumerate(REQUIRED_FORMATS, 1):
        rows[number - 1] = _row(number, doc_format=doc_format)
    return rows


def test_selection_is_stable_and_excludes_only_the_final_doc_id():
    selected, record = select_rows(list(reversed(_source_rows())))

    assert len(selected) == 1000
    assert selected[0]["doc_id"] == "MS1-0001"
    assert selected[-1]["doc_id"] == "MS1-1000"
    assert record["excluded_doc_ids"] == ["MS1-1001"]
    assert "independent of grade" in record["selection_rule"]


def test_selection_refuses_a_source_with_the_wrong_cardinality():
    with pytest.raises(ValueError, match="exactly 1001"):
        select_rows(_source_rows()[:-1])


def test_selection_refuses_when_a_required_document_type_is_absent():
    rows = _source_rows()
    rows[0] = _row(1, doc_format="보고서")
    with pytest.raises(ValueError, match="응시자 지원서"):
        select_rows(rows)
