from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import prepare_full1001_review_batch as batch  # noqa: E402


def _write_report(root: Path, *, failed_id: str = "") -> None:
    documents = root / "documents"
    documents.mkdir(parents=True)
    formats = ["응시자 지원서", "고용계약서", "매뉴얼", "각종 계약서"]
    plan, audit = [], []
    for number in range(1, 1002):
        doc_id = f"RF-{number:04d}"
        doc_format = formats[number - 1] if number <= len(formats) else "보고서"
        item = {"doc_id": doc_id, "department": "기획", "grade": "S2", "doc_format": doc_format,
                "length_min": 1, "length_max": 100, "include_table": False}
        plan.append(item)
        audit.append({**item, "body_len": 10, "length_ok": doc_id != failed_id, "table_actual": False,
                      "table_ok": True, "avoid_hits": [], "grade_token": False, "self_decl": False, "pii": False})
        (documents / f"{doc_id}.txt").write_text(f"제목 {doc_id}\n\n본문", encoding="utf-8")
    (root / "plan.json").write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    (root / "audit_results.json").write_text(json.dumps(audit, ensure_ascii=False), encoding="utf-8")


def test_validated_rf_source_selects_the_stable_first_thousand(tmp_path: Path) -> None:
    _write_report(tmp_path)
    rows, provenance = batch.load_validated_source(tmp_path)
    selected, selection = batch.select_rows(rows)

    assert len(rows) == 1001
    assert len(selected) == 1000
    assert selected[0]["doc_id"] == "RF-0001"
    assert selected[-1]["doc_id"] == "RF-1000"
    assert selection["excluded_doc_ids"] == ["RF-1001"]
    assert provenance["quality_gate"]["pii"] == 0


def test_rf_source_refuses_any_failed_quality_gate(tmp_path: Path) -> None:
    _write_report(tmp_path, failed_id="RF-0002")

    with pytest.raises(ValueError, match="length_ok"):
        batch.load_validated_source(tmp_path)
