"""Calibration packages contain evidence requests, never fabricated answers."""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from build_signoff_batch import collect_population, evidence_bundle
from prepare_classification_pilot import prepare


def write_source(path):
    rows = [{"doc_id": f"{g}-{i}", "label": g, "text": f"Example document {g}-{i}. " * 4,
             "document_origin": "synthetic", "predicted": "TS", "confidence": 0.99}
            for g in ("TS", "S1", "S2", "S3") for i in range(6)]
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")


def test_pilot_has_20_blank_answers_and_separate_blind_key(tmp_path):
    source, out = tmp_path / "source.jsonl", tmp_path / "pack"
    write_source(source)
    before = source.read_bytes()
    manifest = prepare(source, out)
    assert manifest["selected_n"] == 20
    assert manifest["old_label_sampling_counts"] == {g: 5 for g in ("TS", "S1", "S2", "S3")}
    assert not manifest["evaluation_allowed"] and not manifest["training_allowed"]
    answers = [json.loads(x) for x in (out / "reviewer/answer_template.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(answers) == 20
    assert all(r["grade"] is None and r["signed_at"] is None and r["signer_id"] is None for r in answers)
    assert (out / "coordinator/blind_key.jsonl").exists()
    assert not (out / "reviewer/blind_key.jsonl").exists()
    assert (out / "reviewer/CASES.md").read_text(encoding="utf-8").count("## SG") == 20
    pack = [json.loads(x) for x in (out / "reviewer/review_pack.jsonl").read_text(encoding="utf-8").splitlines()]
    for row in pack:
        assert "predicted" not in row and "confidence" not in row and "label" not in row
        assert row["policy_context"]["approval_status"] == "unapproved"
        assert row["evidence_bundle"]["system_facts"] == {}
        assert row["evidence_bundle"]["missing_system_fields"]
        assert row["evidence_bundle"]["document_sha256"] == hashlib.sha256(row["text"].encode()).hexdigest()
    assert source.read_bytes() == before
    with pytest.raises(FileExistsError):
        prepare(source, out)


def test_pack_preserves_actual_input_but_not_model_annotations():
    row = {"text": "source body", "metadata": {"access_scope": "restricted",
           "security_marking": "internal", "predicted": "TS"},
           "source_reference": "archive://record-1", "confidence": 0.99}
    result = evidence_bundle(row)
    assert result["system_facts"] == {"access_scope": "restricted", "security_marking": "internal"}
    assert result["evidence_verification"] == "not_verified"
    assert "predicted" not in json.dumps(result)
    assert result["source"]["source_reference"] == "archive://record-1"


def test_default_population_can_include_unlabeled_and_s2(tmp_path, monkeypatch):
    import build_signoff_batch as batch
    monkeypatch.setattr(batch, "POC", tmp_path)
    source = tmp_path / "source.jsonl"
    source.write_text("\n".join(json.dumps(row) for row in [
        {"text": "unlabeled document " * 5, "document_origin": "organization_real"},
        {"text": "s2 document " * 8, "label": "S2", "document_origin": "organization_real"},
    ]), encoding="utf-8")
    pool, _ = collect_population([str(source)], origins={"organization_real"}, grades=set(), train=set())
    assert len(pool) == 2
    assert {r["hidden_label"] for r in pool} == {"", "S2"}


def test_synthetic_pack_reproducible_without_grade_group_order(tmp_path):
    source = tmp_path / "source.jsonl"
    write_source(source)
    a = prepare(source, tmp_path / "a")
    b = prepare(source, tmp_path / "b")
    assert a == b
    assert (tmp_path / "a/reviewer/review_pack.jsonl").read_bytes() == (tmp_path / "b/reviewer/review_pack.jsonl").read_bytes()


def test_policy_preview_does_not_invent_approved_answers():
    from prepare_policy_approval_table import build_preview
    rows = build_preview()
    assert len(rows) == 64
    assert sum("unknown" not in (r["s"], r["v"], r["m"]) for r in rows) == 27
    assert all(r["approval_status"] == "unapproved" and r["approved_grade"] is None
               and r["approved_by"] is None and r["preview_is_not_test_oracle"] for r in rows)
