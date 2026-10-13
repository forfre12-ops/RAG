"""Checks for an offline diagnostic harness, not model-quality acceptance tests."""
from pathlib import Path
import json
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from measure_svm_ablation import load_cases, metrics, paired_delta, write_json  # noqa: E402


def record(truth="S2", predicted="S2", status="staging"):
    return {"doc_id": "1", "truth": truth, "predicted": predicted, "model_grade": predicted,
            "status": status, "text_sha256": "a" * 64, "cleaned_sha256": "b" * 64}


def test_s2_downward_error_remains_an_error_when_reviewed():
    result = metrics([record(predicted="S3", status="needs_review")])
    assert result["s2_to_s3"] == 1
    assert result["label_agreement"] == 0
    assert result["review_n"] == 1
    assert result["not_routed_error_rate"] is None


def test_same_grades_but_review_changes_not_counted_as_quality_gain():
    result = paired_delta([record(predicted="S3")], [record(predicted="S3", status="needs_review")])
    assert result["grade_changes"] == 0
    assert result["status_changes"] == 1
    assert result["agreement_delta_pp"] == 0


@pytest.mark.parametrize("key", ["doc_id", "truth", "text_sha256", "cleaned_sha256"])
def test_paired_binding_fails_on_any_input_difference(key):
    candidate = record()
    candidate[key] = "different"
    with pytest.raises(ValueError, match="mismatch"):
        paired_delta([record()], [candidate])


def test_partial_measurements_rejected():
    with pytest.raises(ValueError, match="Incomplete"):
        paired_delta([record()], [])


def test_fixed_and_broken_counted_separately():
    baseline = [record(predicted="S3"), {**record(), "doc_id": "2"}]
    candidate = [record(), {**record(predicted="S3"), "doc_id": "2"}]
    result = paired_delta(baseline, candidate)
    assert result["fixed_relative_to_labels"] == result["broken_relative_to_labels"] == 1
    assert result["agreement_delta_pp"] == 0


def test_input_without_id_is_bound_to_file_and_row(tmp_path):
    path = tmp_path / "input.jsonl"
    path.write_text(json.dumps({"text": "plain example", "label": "S3"}) + "\n", encoding="utf-8")
    cases, info = load_cases(path)
    assert cases[0]["doc_id"] == "row-1"
    assert cases[0]["row_number"] == 1
    assert info["missing_source_id"] == 1
    assert info["customer_acceptance_eligible"] is False


@pytest.mark.parametrize("rows", [
    [{"doc_id": "x", "text": "a", "label": "S1"}, {"doc_id": "x", "text": "b", "label": "S2"}],
    [{"text": "", "label": "S1"}],
    [{"text": "a", "label": "unknown"}],
])
def test_invalid_input_not_silently_skipped(tmp_path, rows):
    path = tmp_path / "input.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    with pytest.raises(ValueError):
        load_cases(path)


def test_refuse_overwrite(tmp_path):
    path = tmp_path / "report.json"
    write_json(path, {"existing": True})
    with pytest.raises(FileExistsError):
        write_json(path, {"existing": False})
    assert json.loads(path.read_text())["existing"] is True


def test_only_s3_set_reports_absent_grade_support():
    result = metrics([record(truth="S3", predicted="S3")])
    assert result["label_agreement"] == 1
    assert result["s2_support"] == result["high_support"] == 0
    assert result["per_grade"]["TS"]["support"] == 0
    assert result["macro_f1_fixed_four_classes"] == 0.25
