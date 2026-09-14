"""Review serialization/binding safety; not semantic accuracy tests."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "scripts"))

import prepare_content_reference_review as review  # noqa: E402
from content_reference_review_notes import DEV_NOTES  # noqa: E402
from evaluation_inputs import sha256  # noqa: E402


@pytest.fixture
def sources():
    return review.source_inputs()


@pytest.fixture
def records(sources):
    docs, answers, historical, _ = sources
    return review.development_reviews(docs, answers), review.legacy_reviews(historical)


def test_all_94_have_exact_evidence_and_not_approved(sources, records):
    dev, legacy = records
    result = review.validate_reviews(dev, legacy, sources[0], sources[2])
    assert result["development_reviewed"] == 90 and result["legacy_reviewed"] == 4
    assert result["development_dispositions"] == {"amend_material": 40, "keep": 40, "needs_context": 10}
    assert result["legacy_dispositions"] == {"needs_context": 3, "propose_under_reference": 1}
    assert result["exact_evidence_spans"] == 101
    assert result["development_grade_changes_proposed"] == 0
    assert not result["model_accuracy_measured"] and not result["independent_human_review"]


def test_material_amendments_are_not_40_label_errors(records):
    dev, _ = records
    amendments = [r for r in dev if r["disposition"] == "amend_material"]
    assert len(amendments) == 40
    assert all(r["proposal"]["grade"] == r["source_answer"]["grade"] for r in amendments)
    assert all(r["original_label_replacement"] is None for r in amendments)


def test_three_legacy_abstentions_and_one_new_reference_proposal(records):
    legacy = {r["doc_id"]: r for r in records[1]}
    assert legacy["823545b7edf3a0ef"]["proposal"]["grade"] == "S1"
    assert legacy["9a4ace0da18602c1"]["proposal"]["grade"] is None
    assert legacy["6ea073680b55d1e9"]["proposal"]["grade"] is None
    assert legacy["a1beb524ceafe108"]["proposal"]["grade"] is None
    assert all(r["original_label_replacement"] is None for r in legacy.values())
    assert all(not r["historical_policy_version_verified"] for r in legacy.values())
    assert all(not r["historical_legal_reference_verified"] for r in legacy.values())


def test_sealed_bodies_not_parsed_for_this_review(monkeypatch):
    original = review.read_rows
    opened = []
    def guarded(path):
        opened.append(str(path))
        assert "sealed_candidate" not in str(path)
        return original(path)
    monkeypatch.setattr(review, "read_rows", guarded)
    docs, _, _, protected = review.source_inputs()
    assert len(docs) == 90
    assert any("sealed_candidate" in str(p) for p in protected)
    assert opened


@pytest.mark.parametrize("mutation", ["quote", "span", "hash", "policy", "signature", "gold", "training", "independent", "issue", "grade", "replacement"])
def test_corrupt_review_rejected(sources, records, mutation):
    dev, legacy = copy.deepcopy(records)
    row = dev[0]
    if mutation == "quote":
        row["evidence"][0]["quote"] = "다른 인용"
    elif mutation == "span":
        row["evidence"][0]["start"] = -1
    elif mutation == "hash":
        row["text_sha256"] = "0" * 64
    elif mutation == "policy":
        row["reference_policy"]["version"] = "unrelated-policy"
    elif mutation == "signature":
        row["human_signature"] = "fabricated"
    elif mutation == "gold":
        row["gold_qualified"] = True
    elif mutation == "training":
        row["training_allowed"] = True
    elif mutation == "independent":
        row["independent_human_review"] = True
    elif mutation == "issue":
        row["policy_issue_ids"] = ["CPR-I99"]
    elif mutation == "grade":
        row["proposal"]["grade"] = "S3"
    elif mutation == "replacement":
        row["original_label_replacement"] = "TS"
    with pytest.raises(ValueError):
        review.validate_reviews(dev, legacy, sources[0], sources[2])


def test_missing_or_duplicated_authored_note_rejected(sources, monkeypatch):
    monkeypatch.setattr(review, "DEV_NOTES", DEV_NOTES[:-1] + [DEV_NOTES[0]])
    with pytest.raises(ValueError, match="coverage"):
        review.development_reviews(sources[0], sources[1])


def test_changed_expected_answer_is_not_silently_reused(sources):
    docs, answers, _, _ = copy.deepcopy(sources)
    answers[DEV_NOTES[0][0]]["expected_grade"] = "S3"
    with pytest.raises(ValueError, match="source answer"):
        review.development_reviews(docs, answers)


def test_nonexistent_or_ambiguous_quote_rejected():
    with pytest.raises(ValueError):
        review.evidence("본문", "없는 근거", "test")
    with pytest.raises(ValueError):
        review.evidence("반복 반복", "반복", "test")


def test_pinned_source_version_cannot_drift(monkeypatch):
    monkeypatch.setattr(review, "POLICY_SHA", "0" * 64)
    with pytest.raises(ValueError, match="pinned"):
        review.source_inputs()


def test_all_ten_issues_linked_and_no_grading_algorithm_called(sources, records):
    dev, legacy = records
    result = review.validate_reviews(dev, legacy, sources[0], sources[2])
    assert set(result["issue_affected_review_counts"]) == {f"CPR-I{i:02d}" for i in range(1, 11)}
    assert result["sealed_content_semantically_reviewed"] is False


def test_output_cannot_be_inside_frozen_pack():
    with pytest.raises(ValueError, match="frozen"):
        review.build(review.PACK / "forbidden_review_output")


def test_generated_files_roundtrip_and_input_preservation(tmp_path, sources):
    out = tmp_path / "review"
    result = review.build(out)
    assert result["development_reviewed"] == 90
    assert len(review.read_rows(out / "development_review90.jsonl")) == 90
    assert len(review.read_rows(out / "legacy_review4.jsonl")) == 4
    assert (out / "DEVELOPMENT_REVIEW90.md").read_text(encoding="utf-8").count("\n## CR-") == 90
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert all(sha256(out / f["path"]) == f["sha256"] for f in manifest["files"])
    assert all(sha256(path) == digest for path, digest in sources[3].items())
    with pytest.raises(ValueError, match="Output exists"):
        review.build(out)
