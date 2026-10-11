from __future__ import annotations

import json

import pytest

from koipa.services.candidate_classifier_scores import (
    SCORE_SCHEMA_VERSION,
    SCORE_SNAPSHOT_NAME,
    load_score_index,
    score_for_document,
    text_sha256,
    write_score_snapshot,
)


def _record(*, doc_id: str = "RF-0001", text: str = "본문", **overrides) -> dict:
    record = {
        "schema_version": SCORE_SCHEMA_VERSION,
        "doc_id": doc_id,
        "document_sha256": text_sha256(text),
        "model_version": "test-model-v1",
        "scored_at": "2026-10-11T00:00:00+00:00",
        "score_basis": "serving_pipeline",
        "predicted_label": "S1",
        "scores": {"TS": 0.1, "S1": 0.7, "S2": 0.15, "S3": 0.05},
        "calibrated": False,
    }
    record.update(overrides)
    return record


def test_score_snapshot_is_hash_bound_and_only_exposes_a_complete_distribution(tmp_path):
    snapshot = write_score_snapshot(tmp_path, [_record()])
    assert snapshot == tmp_path / SCORE_SNAPSHOT_NAME
    assert len(snapshot.read_text(encoding="utf-8").splitlines()) == 1

    index = load_score_index(tmp_path)
    available = score_for_document(index, doc_id="RF-0001", document_sha256=text_sha256("본문"))
    assert available == {
        "status": "available", "scores": {"TS": 0.1, "S1": 0.7, "S2": 0.15, "S3": 0.05},
        "predicted_label": "S1", "model_version": "test-model-v1",
        "scored_at": "2026-10-11T00:00:00+00:00", "score_basis": "serving_pipeline", "calibrated": False,
    }
    stale = score_for_document(index, doc_id="RF-0001", document_sha256=text_sha256("바뀐 본문"))
    assert stale == {"status": "unavailable", "reason": "score_snapshot_stale_for_document"}


@pytest.mark.parametrize("override", [
    {"scores": {"TS": 0.5, "S1": 0.5}},
    {"scores": {"TS": 0.2, "S1": 0.5, "S2": 0.15, "S3": 0.05}},
    {"predicted_label": "TS"},
    {"score_basis": "rule_fallback"},
])
def test_snapshot_refuses_partial_or_misleading_records(tmp_path, override):
    with pytest.raises(ValueError, match="invalid or duplicate"):
        write_score_snapshot(tmp_path, [_record(**override)])
    assert not (tmp_path / SCORE_SNAPSHOT_NAME).exists()


def test_invalid_existing_line_is_fail_closed(tmp_path):
    path = tmp_path / SCORE_SNAPSHOT_NAME
    path.write_text(json.dumps(_record(scores={"TS": 1.0, "S1": 0.0, "S2": 0.0, "S3": 0.0})) + "\n", encoding="utf-8")
    assert score_for_document(load_score_index(tmp_path), doc_id="RF-0001", document_sha256=text_sha256("본문")) == {
        "status": "unavailable", "reason": "score_snapshot_missing_or_stale"}
