"""Regression tests for the failures reproduced in the 2026-09-14 audit."""
from __future__ import annotations

import json
import hashlib
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import measure_four_metrics as metrics
from audit_eval_ground_truth import tier_of
import pytest


def test_s2_to_s3_cannot_be_invisible():
    good = {"truth": "S2", "predicted": "S2", "model_grade": "S2", "status": "staging"}
    bad = {**good, "predicted": "S3", "model_grade": "S3"}
    assert metrics.compute([good]) != metrics.compute([bad])


def test_human_tag_alone_is_not_gold():
    assert tier_of({"label_source": "human_review"})[0] != "GOLD"


def test_s3_truth_and_training_overlap_are_checked(tmp_path, monkeypatch):
    row = {"text": "public document", "label": "S3", "label_source": "synthetic_llm"}
    path = tmp_path / "rows.jsonl"
    path.write_text(json.dumps(row) + "\n", encoding="utf-8")
    monkeypatch.setattr(metrics, "TRAIN_POOLS", (str(path),))
    tier, overlap, counts = metrics.suite_context(path)
    assert tier.value == "BRONZE"
    assert overlap == 1
    assert counts == {"BRONZE": 1}


def test_exact_grade_and_high_grade_detection_are_distinct():
    result = metrics.compute([{"truth": "TS", "predicted": "S1", "model_grade": "S1", "status": "staging"}])
    assert result["exact_grade_accuracy"]["rate"] == 0
    assert result["high_grade_detection_recall"]["rate"] == 1
    assert result["per_grade"]["TS"]["recall"] == 0
    assert result["confusion_matrix"]["TS"]["S1"] == 1


def test_review_routing_does_not_repair_grade():
    result = metrics.compute([{"truth": "S2", "predicted": "S3", "model_grade": "S3", "status": "needs_review"}])
    assert result["s2_underclass"]["rate"] == 1
    assert result["exact_grade_error"]["rate"] == 1
    assert result["not_routed_error"]["rate"] is None
    assert result["review_load"]["rate"] == 1


def test_unknown_truth_is_not_silently_dropped():
    with pytest.raises(ValueError):
        metrics.compute([{"truth": "???", "predicted": "S3"}])


def test_legacy_signed_synthetic_without_policy_is_not_gold():
    row = {"label_source": "human_review", "reviewer_id": "policy_reviewer_1",
           "reviewer_ids": ["policy_reviewer_1"], "gate_version": "human_signoff_v1",
           "signed_at": "2026-09-14T00:00:00Z", "document_origin": "synthetic"}
    assert tier_of(row)[0] == "UNKNOWN"


def test_strict_jsonl_rejects_malformed_row(tmp_path):
    from evaluation_inputs import read_rows
    path = tmp_path / "bad.jsonl"
    path.write_text('{"doc_id":"ok"}\n{broken}\n', encoding="utf-8")
    with pytest.raises(ValueError, match="bad.jsonl:2"):
        read_rows(path)


def test_record_binding_rejects_subset_duplicate_relabel_and_body_change():
    from evaluation_inputs import record_binding
    data = [{"doc_id": "d1", "text": "one", "label": "S2"},
            {"doc_id": "d2", "text": "two", "label": "S3"}]
    rows = [{"doc_id": d["doc_id"], "truth": d["label"],
             "text_sha256": hashlib.sha256(d["text"].encode()).hexdigest()} for d in data]
    assert not record_binding(rows, data)
    assert record_binding(rows[:1], data)
    assert record_binding(rows + rows[:1], data)
    assert record_binding([{**rows[0], "truth": "S3"}, rows[1]], data)
    assert record_binding([{**rows[0], "text_sha256": "0" * 64}, rows[1]], data)


def test_candidate_training_manifest_checks_s3_and_family(tmp_path):
    from evaluation_inputs import check_training, sha256
    train = tmp_path / "train.jsonl"
    train.write_text(json.dumps({"text": "same", "label": "S3", "family_id": "family-1"}) + "\n", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"schema_version": "training-inputs-v1", "model_id": "candidate",
        "complete": True, "files": [{"path": "train.jsonl", "sha256": sha256(train)}]}), encoding="utf-8")
    result = check_training(tmp_path, manifest, [{"text": "same", "label": "S3", "family_id": "family-1"}])
    assert result["checked"] is True and result["overlap"] == result["family_overlap"] == 1
    result = check_training(tmp_path, manifest, [{"text": "different", "label": "S3", "family_id": "family-1"}])
    assert result["family_overlap"] == 1 and result["overlap"] == 0


def test_measure_and_judge_end_to_end_offline(tmp_path, monkeypatch):
    import judge_model_candidate as candidate
    from evaluation_inputs import sha256
    monkeypatch.setattr(candidate, "POC", tmp_path)
    evaluation = tmp_path / "eval.jsonl"
    evaluation.write_text(json.dumps({"doc_id": "eval-1", "text": "evaluation body",
        "family_id": "eval-family", "label": "S2", "label_source": "synthetic_llm"}) + "\n", encoding="utf-8")
    train = tmp_path / "train.jsonl"
    train.write_text(json.dumps({"text": "training body", "label": "S2", "family_id": "train-family"}) + "\n", encoding="utf-8")
    manifest = tmp_path / "training.json"
    manifest.write_text(json.dumps({"schema_version": "training-inputs-v1", "complete": True,
        "model_id": "model-1", "files": [{"path": "train.jsonl", "sha256": sha256(train)}]}), encoding="utf-8")

    def measure(prediction, name):
        record = {"doc_id": "eval-1", "truth": "S2", "predicted": prediction,
            "model_grade": prediction, "status": "staging",
            "text_sha256": hashlib.sha256(b"evaluation body").hexdigest()}
        path = tmp_path / name
        path.write_text(json.dumps(record) + "\n", encoding="utf-8")
        context = {"org_id": "test-org", "policy_version": "test-policy", "model_id": "model-1",
            "measurement_config_sha256": "c" * 64, "records_sha256": sha256(path), "eval_sha256": sha256(evaluation)}
        return candidate.measure("reference", name, "eval.jsonl", {}, training_manifest=manifest, context=context)

    good, bad = measure("S2", "good.jsonl"), measure("S3", "bad.jsonl")
    assert good["usable_for_judgement"] is True, good
    assert candidate.judge({"faces": [good]}, [good])["verdict"] == "REGRESSION_OK"
    assert candidate.judge({"faces": [good]}, [bad])["verdict"] == "REJECT"
    assert candidate.measure("reference", "good.jsonl", "eval.jsonl", {})["usable_for_judgement"] is False


def test_rejected_or_training_intended_human_answer_cannot_be_gold():
    from koipa.golden_tiers import evaluation_signoff_gaps
    assert "rejected_by_reviewer" in evaluation_signoff_gaps({"signoff_rejected": {"reviewer_id": "human"}})
    assert "designated_for_training_not_evaluation" in evaluation_signoff_gaps({"intended_use": "train"})


def test_recorded_gold_envelope_is_checked_not_just_source_tag():
    # A fictitious unit-test record: this tests shape/binding, NOT approval authenticity.
    row = {"label_source": "human_review", "label": "S2", "reviewer_id": "admin_kim",
        "reviewer_ids": ["admin_kim"], "gate_version": "human_signoff_v1",
        "signed_at": "2026-09-14T00:00:00Z", "document_origin": "public_real",
        "policy_version": "test-policy", "rule_id": "test-rule", "text": "test body",
        "not_higher_reason": "unit test", "not_lower_reason": "unit test",
        "document_sha256": hashlib.sha256(b"test body").hexdigest(),
        "evaluation_scope": "reference",
        "policy_approval": {"status": "approved", "reference": "test-only", "sha256": "a" * 64},
        "decision_evidence": [{"fact": "unit-test-fact", "reference": "test-only", "source_kind": "document",
                               "observed_at": "2026-09-14T00:00:00Z", "sha256": "b" * 64}]}
    assert tier_of(row)[0] == "GOLD"
    for key in ("policy_version", "policy_approval", "decision_evidence", "document_sha256", "rule_id"):
        missing = dict(row)
        missing.pop(key)
        assert tier_of(missing)[0] != "GOLD", key
    assert tier_of({**row, "signoff_rejected": {"reviewer_id": "admin_kim"}})[0] != "GOLD"
    assert tier_of({**row, "evaluation_scope": "customer"})[0] != "GOLD"
    # Synthetic reference truth is possible, but cannot masquerade as customer truth.
    assert tier_of({**row, "document_origin": "synthetic"})[0] == "GOLD"
    assert tier_of({**row, "document_origin": "synthetic", "evaluation_scope": "customer"})[0] != "GOLD"
