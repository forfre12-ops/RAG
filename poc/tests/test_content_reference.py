"""Safety/consistency tests, not model-accuracy or human-approval evidence."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "scripts"))

from content_reference_contract import (  # noqa: E402
    PREDICATES, known_pool_overlap, load_pack, measure_predictions, resolve, validate_pack,
)
from evaluation_inputs import sha256  # noqa: E402
from prepare_content_reference import (  # noqa: E402
    POLICY, build, build_cases, comparison_contract, legacy_conflicts, write_jsonl,
)


@pytest.fixture
def pack():
    return build_cases(sha256(POLICY))


def predictions_for(inputs, answers):
    expected = {a["doc_id"]: a for a in answers}
    return [{k: d[k] for k in ("doc_id", "text_sha256", "policy_version", "policy_sha256")} |
            {"predicted": expected[d["doc_id"]]["expected_grade"],
             "model_grade": expected[d["doc_id"]]["expected_grade"],
             "status": "staging" if expected[d["doc_id"]]["expected_status"] == "recommended" else "needs_review"}
            for d in inputs]


def test_complete_counts_policy_evidence_and_no_gold(pack):
    result = validate_pack(*pack, POLICY)
    assert result["n"] == 140
    assert result["grade_cases"] == 120
    assert result["review_cases"] == 20
    assert result["families"] == 40
    assert result["gold_qualified"] == 0
    assert result["split"]["development"]["n"] == 90
    assert result["split"]["sealed_candidate"]["n"] == 50
    assert result["split"]["development"]["statuses"]["needs_evidence"] == 5
    assert result["split"]["sealed_candidate"]["statuses"]["needs_policy_review"] == 5


def test_deterministic_no_answer_fields_in_input(pack):
    assert pack == build_cases(sha256(POLICY))
    for row in pack[0]:
        assert not {"label", "grade", "facts", "rule_ids", "expected_grade", "variant_index"} & row.keys()
        assert "variant_index" not in row["source"]
        assert row["source"]["real_document"] is False


@pytest.mark.parametrize("predicate,grade", [("core_package", "TS"), ("live_control", "TS"),
                                          ("bulk_sensitive", "TS"), ("specific", "S1"),
                                          ("internal", "S2"), ("generic", "S3")])
def test_single_predicate(predicate, grade):
    facts = dict.fromkeys(PREDICATES, False) | {predicate: True}
    assert resolve(facts)["grade"] == grade


def test_unknown_and_no_match_do_not_default_s3():
    assert resolve(dict.fromkeys(PREDICATES, None))["status"] == "needs_evidence"
    assert resolve(dict.fromkeys(PREDICATES, False))["grade"] is None
    facts = dict.fromkeys(PREDICATES, False) | {"bulk_sensitive": None, "specific": True}
    assert resolve(facts)["candidate_grades"] == ["TS", "S1"]


def test_known_ts_unaffected_by_lower_unknown_and_multiple_positive_rules():
    facts = dict.fromkeys(PREDICATES, None) | {"core_package": True, "live_control": True}
    assert resolve(facts)["grade"] == "TS"
    assert resolve(facts)["rule_ids"] == ["CP-TS-01", "CP-TS-02"]


def test_conflict_not_silent_highest_wins():
    result = resolve(dict.fromkeys(PREDICATES, True), conflict=True, conflict_candidates=["S3", "TS"])
    assert result["grade"] is None and result["status"] == "needs_policy_review"


@pytest.mark.parametrize("bad", [0, 1, "false", "unknown", 0.0])
def test_no_numeric_or_string_fact_substitutes(bad):
    with pytest.raises(ValueError, match="bool or unknown"):
        resolve(dict.fromkeys(PREDICATES, False) | {"generic": bad})


@pytest.mark.parametrize("mutation,match", [
    ("body", "Body SHA"), ("policy", "Policy binding"), ("answer_hash", "Answer binding"),
    ("span", "Invalid span"), ("quote", "quote mismatch"), ("grade", "contradicts"),
    ("rule", "rules/candidate"), ("gold", "authority"), ("unknown_zero", "Unknown"),
    ("input_label", "leakage"), ("id_duplicate", "document IDs"),
])
def test_fail_closed_tampering(pack, mutation, match):
    inputs, answers, splits = copy.deepcopy(pack)
    if mutation == "body":
        inputs[0]["text"] += "변경"
    elif mutation == "policy":
        answers[0]["policy_sha256"] = "0" * 64
    elif mutation == "answer_hash":
        answers[0]["text_sha256"] = "0" * 64
    elif mutation == "span":
        answers[0]["evidence"][0]["end"] += 1
    elif mutation == "quote":
        answers[0]["evidence"][0]["quote"] = "다른 인용"
    elif mutation == "grade":
        answers[0]["expected_grade"] = "S3"
    elif mutation == "rule":
        answers[0]["rule_ids"] = ["CP-S3-01"]
    elif mutation == "gold":
        answers[0]["human_signature"] = "invented"
    elif mutation == "unknown_zero":
        answers[0]["facts"]["generic"]["value"] = 0
    elif mutation == "input_label":
        inputs[0]["label"] = "TS"
    elif mutation == "id_duplicate":
        inputs[0]["doc_id"] = inputs[1]["doc_id"]
    with pytest.raises(ValueError, match=match):
        validate_pack(inputs, answers, splits, POLICY)


def test_family_split_leakage_is_rejected(pack):
    inputs, answers, splits = copy.deepcopy(pack)
    # Swap one whole-family ID assignment on a case, preserving body/grade counts.
    dev_id, seal_id = splits["development"][0], splits["sealed_candidate"][0]
    family = next(d["family_id"] for d in inputs if d["doc_id"] == dev_id)
    for row in inputs + answers:
        if row["doc_id"] == seal_id:
            row["family_id"] = family
    with pytest.raises(ValueError, match="Family"):
        validate_pack(inputs, answers, splits, POLICY)


def test_perfect_fixture_is_only_diagnostic(pack):
    inputs, answers, _ = pack
    result = measure_predictions(inputs, answers, predictions_for(inputs, answers))
    assert result["grade_accuracy_including_abstentions"] == {"count": 120, "n": 120, "rate": 1.0}
    assert result["routing"]["tp"] == 20 and result["routing"]["fn"] == 0
    assert result["grade_matrix_denominator"] == 120
    assert not result["customer_accuracy_claim_allowed"] and not result["model_promotion_allowed"]


def test_grade_errors_not_hidden_by_review_routing(pack):
    inputs, answers, _ = pack
    predictions = predictions_for(inputs, answers)
    index = {p["doc_id"]: p for p in predictions}
    # Change one S1 to S2 (under), one S2 to S1 (over), S2 to S3, TS to S3, S3 to S2.
    changed = set()
    for truth, pred in (("S1", "S2"), ("S2", "S1"), ("S2", "S3"), ("TS", "S3"), ("S3", "S2")):
        a = next(a for a in answers if a["expected_grade"] == truth and a["doc_id"] not in changed)
        changed.add(a["doc_id"])
        index[a["doc_id"]].update(predicted=pred, model_grade=pred, status="needs_review")
    review = next(a for a in answers if a["expected_grade"] is None)
    index[review["doc_id"]].update(predicted="S3", model_grade="S3", status="staging")
    result = measure_predictions(inputs, answers, predictions)
    assert result["grade_accuracy_including_abstentions"]["count"] == 115
    for metric in ("s1_to_s2", "s2_to_s1", "s2_to_s3", "ts_s1_to_s3", "s3_overclassification"):
        assert result[metric]["count"] == 1
    assert result["routing"]["fn"] == 1 and result["routing"]["fp"] == 5


def test_grade_abstention_cannot_inflate_full_accuracy(pack):
    inputs, answers, _ = pack
    predictions = predictions_for(inputs, answers)
    predictions[0].update(predicted=None, model_grade=None, status="needs_review")
    result = measure_predictions(inputs, answers, predictions)
    assert result["grade_accuracy_including_abstentions"]["n"] == 120
    assert result["grade_accuracy_including_abstentions"]["count"] == 119
    assert result["grade_matrix_denominator"] == 119
    assert result["grade_abstentions"]["count"] == 1


@pytest.mark.parametrize("field,value", [("policy_version", "old"), ("text_sha256", "0" * 64),
                                       ("policy_sha256", "1" * 64), ("predicted", "GOLD"), ("status", "confirmed")])
def test_prediction_binding_and_status_strict(pack, field, value):
    inputs, answers, _ = pack
    predictions = predictions_for(inputs, answers)
    predictions[0][field] = value
    with pytest.raises(ValueError):
        measure_predictions(inputs, answers, predictions)


def test_prediction_missing_duplicate_not_silently_dropped(pack):
    inputs, answers, _ = pack
    predictions = predictions_for(inputs, answers)
    with pytest.raises(ValueError, match="coverage"):
        measure_predictions(inputs, answers, predictions[:-1])
    with pytest.raises(ValueError, match="coverage"):
        measure_predictions(inputs, answers, predictions[:-1] + [predictions[0]])


def test_training_overlap_scope_is_honest(tmp_path, pack):
    write_jsonl(tmp_path / "known.jsonl", [{"text": pack[0][0]["text"], "label": "TS"}])
    result = known_pool_overlap(pack[0], tmp_path, ["known.jsonl", "missing.jsonl"])
    assert result["overlap_candidate_ids"] == [pack[0][0]["doc_id"]]
    assert result["pools"][0]["rows_without_family_id"] == 1
    assert result["pools"][1]["status"] == "MISSING"
    assert not result["complete_model_training_lineage"]


def test_legacy_conflicts_preserve_original_and_no_replacement():
    issues = legacy_conflicts(POC)
    assert len(issues) == 4
    assert all(i["replacement_label"] is None and i["raw_body_copied"] is False for i in issues)
    assert all(i["sides"][0]["label"] != i["sides"][1]["label"] for i in issues)


def test_training_plan_never_enables_training():
    contract = comparison_contract()
    assert contract["training_enabled"] is False
    assert contract["experiment_ab"]["only_changed_variable"] == "label"
    assert len(contract["excluded_inputs"]) == 4


def test_disk_roundtrip_no_overwrite_manifest_and_hashes(tmp_path):
    out = tmp_path / "pack"
    build(out)
    inputs, answers, splits, checked = load_pack(out, POLICY)
    assert checked["n"] == len(inputs) == len(answers) == 140
    assert len(splits["development"]) == 90
    with pytest.raises(ValueError, match="Output exists"):
        build(out)
    # Fixture mutation deliberately corrupts a generated temporary artifact.
    target = out / "development/inputs.jsonl"
    target.write_text(target.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="hash mismatch"):
        load_pack(out, POLICY)


def test_manifest_cannot_omit_data_files(tmp_path):
    out = tmp_path / "pack"
    build(out)
    target = out / "manifest.json"
    manifest = json.loads(target.read_text(encoding="utf-8"))
    manifest["files"] = []
    target.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ValueError, match="coverage"):
        load_pack(out, POLICY)
