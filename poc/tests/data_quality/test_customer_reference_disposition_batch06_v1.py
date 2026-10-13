"""Original64 disposition bindings, specific scope holds and no release claims."""
from __future__ import annotations

import copy
import json
import shutil
from collections import Counter
from pathlib import Path

import pytest

import customer_reference_disposition_batch06_v1 as review
from koipa.policy_facts import FactContractError, text_digest, value_digest


@pytest.fixture(scope="module")
def records():
    return review.compile_dispositions()


@pytest.fixture(scope="module")
def originals():
    records, answers, details = review._read_parent(review.DEFAULT_PARENT)
    return ({r["input"]["doc_id"]: r for r in records},
            {r["doc_id"]: r for r in answers}, {r["doc_id"]: r for r in details})


@pytest.mark.parametrize("index", range(64))
def test_each_original_has_specific_bound_review(index, records, originals):
    row = records[index]
    source = originals[0][row["doc_id"]]
    assert source["family_id"] == review.REVIEWS[index]["family_id"]
    assert row["input_sha256"] == value_digest(source["input"])
    assert row["body_sha256"] == text_digest(source["input"]["text"])
    assert row["answer_sha256"] == value_digest(originals[1][row["doc_id"]])
    assert row["evidence_sha256"] == value_digest(originals[2][row["doc_id"]])
    assert row["reference_grade"] == originals[1][row["doc_id"]]["reference_grade"]
    assert row["source_manifest_sha256"] == review.PARENT_MANIFEST
    assert row["policy_sha256"] == review.POLICY_SHA256
    assert row["reviewer_kind"] == "ai_internal_review"
    assert row["review_scope"] == "body_and_synthetic_context"
    assert row["source_unchanged"] is True
    assert all(row[flag] is False for flag in review.FLAGS)
    for key in ("body_review_note", "context_review_note", "decision_reason"):
        assert len(row[key]) >= 40
    assert len(row["required_action"]) >= 10
    assert len({e["quote"] for e in row["body_evidence"]}) >= 2
    assert set(review.REVIEWS[index]["quotes"]) <= {e["quote"] for e in row["body_evidence"]}
    for evidence in row["body_evidence"]:
        assert source["input"]["text"][evidence["start"]:evidence["end"]] == evidence["quote"]
        assert evidence["sha256"] == text_digest(evidence["quote"])
    context = {c["name"]: c["value"] for c in source["input"]["context"]}
    assert {e["name"] for e in row["context_evidence"]} == {
        "reader_scope", "impact_description", "management_controls"}
    for evidence in row["context_evidence"]:
        assert evidence["quote"] == context[evidence["name"]]
        assert evidence["sha256"] == value_digest(evidence["quote"])
    assert row["benchmark_decision"] == "hold"
    assert any(f["scope"] == "benchmark" for f in row["findings"])
    if row["conditional_reference_decision"] == "accept":
        assert row["source_disposition"] == "keep"
        assert all(f["scope"] == "benchmark" for f in row["findings"])
    else:
        assert row["source_disposition"] == "revise"
        assert any(f["scope"] == "conditional_reference" for f in row["findings"])


def test_coverage_unique_notes_and_scope_counts(records):
    assert len(records) == len({r["doc_id"] for r in records}) == 64
    assert len({r["body_review_note"] for r in records}) == 64
    assert len({r["context_review_note"] for r in records}) == 64
    assert Counter(r["reference_grade"] for r in records) == {"TS": 16, "S1": 16, "S2": 16, "S3": 16}
    assert Counter(r["source_disposition"] for r in records) == {"keep": 62, "revise": 2}
    assert Counter(r["conditional_reference_decision"] for r in records) == {"accept": 62, "hold": 2}
    assert Counter(r["benchmark_decision"] for r in records) == {"hold": 64}
    text = json.dumps(records, ensure_ascii=False)
    assert "\ufffd" not in text
    assert "customer_gold" not in text


def test_two_scope_clarifications_are_not_grade_error_or_algorithm_failure_claim(records):
    held = {review.REVIEWS[i]["family_id"]: r for i, r in enumerate(records)
            if r["conditional_reference_decision"] == "hold"}
    assert set(held) == {"family-bleaching-exposure", "family-candidate-prune-order"}
    for row in held.values():
        assert "계산을 오류로 판정한 것은 아니지만" in row["decision_reason"]
        assert "원문은 수정하지" in row["required_action"] or "원문을 수정하지" in row["required_action"]
    assert "초점 조명" in held["family-bleaching-exposure"]["required_action"]
    assert "목적 방향" in held["family-candidate-prune-order"]["required_action"]


def test_named_targets_preserved_and_original_enzyme_remains_accepted(records, originals):
    rows = {review.REVIEWS[i]["family_id"]: r for i, r in enumerate(records)}
    enzyme = rows["family-enzyme-addition-lag"]
    original = originals[0][enzyme["doc_id"]]["input"]["text"]
    assert "효소 E" in original and "기질 배치 M에 한정" in original
    assert enzyme["source_disposition"] == "keep"
    assert "효소 E와 기질 배치 M을 둘 다 보존" in enzyme["required_action"]
    assert rows["family-slot-replenish-wave"]["source_disposition"] == "keep"
    assert "실제 창고 사건의 모든 분류 규약을 검증한 것은 아니다" in rows[
        "family-slot-replenish-wave"]["body_review_note"]


def test_roundtrip_report_no_customer_gold_or_release(tmp_path, records):
    before = review._sha(review.DEFAULT_PARENT / "manifest.json")
    root = tmp_path / "review"
    summary = review.write_report(root)
    assert summary == review.verify_report(root)
    assert before == review._sha(review.DEFAULT_PARENT / "manifest.json")
    assert summary["source_unchanged"] is True
    for key in (*review.FLAGS, "alias_removal_view_adopted", "customer_gold_created", "human_review_certified"):
        assert summary[key] is False
    assert review._rows(root / "dispositions.jsonl") == records
    assert (root / "REVIEW.md").read_text(encoding="utf-8").count("## doc-") == 64
    with pytest.raises(FactContractError, match="output_exists"):
        review.write_report(root)


def test_frozen_ancestor_and_source_pack_cannot_be_output(tmp_path):
    with pytest.raises(FactContractError, match="inside_parent"):
        review._output_guard(review.DEFAULT_PARENT / "not-created", review.DEFAULT_PARENT)
    (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FactContractError, match="inside_frozen_pack"):
        review._output_guard(tmp_path / "not-created", review.DEFAULT_PARENT)
    assert not (tmp_path / "not-created").exists()


def test_parent_hash_drift_fails_before_output(tmp_path):
    parent = tmp_path / "fake-parent"
    parent.mkdir()
    (parent / "manifest.json").write_text("{}", encoding="utf-8")
    with pytest.raises(FactContractError, match="parent_manifest_changed"):
        review.write_report(tmp_path / "output", parent)
    assert not (tmp_path / "output").exists()


def test_unknown_or_ambiguous_quote_fails():
    with pytest.raises(FactContractError, match="quote_missing_or_ambiguous"):
        review._quote("실제 원문", "없는 인용")
    with pytest.raises(FactContractError, match="quote_missing_or_ambiguous"):
        review._quote("반복 반복", "반복")


def test_review_drift_fails_instead_of_reusing_old_status(monkeypatch):
    changed = copy.deepcopy(review.REVIEWS)
    changed[0]["quotes"] = ("아직 검토하지 않은 다른 본문", changed[0]["quotes"][1])
    monkeypatch.setattr(review, "REVIEWS", changed)
    with pytest.raises(FactContractError, match="quote_missing_or_ambiguous"):
        review.compile_dispositions()


def test_report_tamper_fails_even_with_rehashed_payload(tmp_path):
    root = tmp_path / "review"
    review.write_report(root)
    rows = review._rows(root / "dispositions.jsonl")
    rows[0]["training_allowed"] = True
    (root / "dispositions.jsonl").write_text(
        "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows), encoding="utf-8")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"]["dispositions.jsonl"] = review._sha(root / "dispositions.jsonl")
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(FactContractError, match="report_replay"):
        review.verify_report(root)


def test_source_changes_during_compile_prevent_report_completion(tmp_path, monkeypatch):
    original = review._sha
    reads = 0

    def changed_sha(path):
        nonlocal reads
        if Path(path).resolve() == Path(review.__file__).resolve():
            reads += 1
            if reads > 1:
                return "0" * 64
        return original(path)

    monkeypatch.setattr(review, "_sha", changed_sha)
    with pytest.raises(FactContractError, match="source_drift"):
        review.write_report(tmp_path / "not-created")
    assert not (tmp_path / "not-created").exists()


def test_extra_unlisted_file_rejected(tmp_path):
    root = tmp_path / "review"
    review.write_report(root)
    (root / "unlisted.txt").write_text("not part of the reviewed output", encoding="utf-8")
    with pytest.raises(FactContractError, match="unlisted_output"):
        review.verify_report(root)


def test_payload_damage_cannot_create_completed_manifest(tmp_path, monkeypatch):
    original = review._check_written_payload

    def damage_first(root, payload):
        (root / "summary.json").write_text("{}", encoding="utf-8")
        original(root, payload)

    monkeypatch.setattr(review, "_check_written_payload", damage_first)
    root = tmp_path / "incomplete"
    with pytest.raises(FactContractError, match="output_changed_before_manifest"):
        review.write_report(root)
    assert not (root / "manifest.json").exists()


def test_metadata_exact_panel_is_required_even_if_manifest_rebound(tmp_path, monkeypatch):
    root = tmp_path / "parent"
    names = ("authoring/documents.jsonl", "answers/answers.candidate.jsonl", "answers/evidence.jsonl",
             "authoring/batch06_metadata.jsonl", "manifest.json")
    for name in names:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(review.DEFAULT_PARENT / name, root / name)
    metadata = review._rows(root / "authoring/batch06_metadata.jsonl")
    metadata[0]["family_id"] = "family-never-reviewed"
    (root / "authoring/batch06_metadata.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in metadata), encoding="utf-8")
    manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
    manifest["files"]["authoring/batch06_metadata.jsonl"] = review._sha(root / "authoring/batch06_metadata.jsonl")
    (root / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(review, "PARENT_MANIFEST", review._sha(root / "manifest.json"))
    with pytest.raises(FactContractError, match="panel_family_binding"):
        review.compile_dispositions(root)


def test_common_adoption_contract_accepts_all64(records, originals):
    from koipa.customer_benchmark import Document
    from koipa.customer_reference_adoption_v1 import _validate_review

    hard_hold = {r["doc_id"] for r in records}
    for row in records:
        key = row["doc_id"]
        _validate_review(row, Document.model_validate(originals[0][key]),
                         originals[1][key], originals[2][key], hard_hold)
