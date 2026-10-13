"""Bind the authored 196 individual judgments; never test as human gold."""

import copy
import json

import pytest

import customer_reference_disposition_prior196_v1 as review
from koipa.customer_benchmark import FLAGS
from koipa.policy_facts import FactContractError, text_digest, value_digest


@pytest.fixture(scope="module")
def reviewed():
    return review.compile_dispositions()


def original_by_id():
    return {
        r["input"]["doc_id"]: r
        for r in review._rows(review.DEFAULT_PARENT / "authoring/documents.jsonl")
    }


def test_all_prior_documents_individually_reviewed_and_bound(reviewed):
    originals = original_by_id()
    new_ids = {
        r["doc_id"]
        for r in review._rows(
            review.DEFAULT_PARENT / "authoring/batch06_metadata.jsonl"
        )
    }
    assert len(reviewed) == 196
    assert {r["doc_id"] for r in reviewed} == set(originals) - new_ids
    assert len({r["body_review_note"] for r in reviewed}) == 196
    assert len({r["context_review_note"] for r in reviewed}) == 196
    answers = {
        r["doc_id"]: r
        for r in review._rows(review.DEFAULT_PARENT / "answers/answers.candidate.jsonl")
    }
    details = {
        r["doc_id"]: r
        for r in review._rows(review.DEFAULT_PARENT / "answers/evidence.jsonl")
    }
    for row in reviewed:
        doc = originals[row["doc_id"]]
        assert row["body_sha256"] == text_digest(doc["input"]["text"])
        assert row["input_sha256"] == doc["input_sha256"]
        assert row["answer_sha256"] == value_digest(answers[row["doc_id"]])
        assert row["evidence_sha256"] == value_digest(details[row["doc_id"]])
        assert row["reference_grade"] == answers[row["doc_id"]]["reference_grade"]
        assert details[row["doc_id"]]["rationale"] in row["context_review_note"]
        assert row["policy_sha256"] == review.POLICY_SHA256
        assert (
            len(row["body_evidence"])
            == len({e["quote"] for e in row["body_evidence"]})
            == 2
        )
        for evidence in row["body_evidence"]:
            assert (
                doc["input"]["text"][evidence["start"] : evidence["end"]]
                == evidence["quote"]
            )
            assert evidence["sha256"] == text_digest(evidence["quote"])
        context = {c["name"]: c["value"] for c in doc["input"]["context"]}
        assert {c["name"] for c in row["context_evidence"]} == {
            "reader_scope",
            "impact_description",
            "management_controls",
        }
        for evidence in row["context_evidence"]:
            assert evidence["quote"] == context[evidence["name"]]
            assert evidence["sha256"] == value_digest(evidence["quote"])
        assert all(
            len(row[key]) >= 40
            for key in ("body_review_note", "context_review_note", "decision_reason")
        )
        assert all(row[key] is False for key in FLAGS)
        assert row["source_unchanged"] is True
        assert row["reviewer_kind"] == "ai_internal_review"


def test_counts_distinguish_conditional_truth_from_benchmark_suitability(reviewed):
    summary = review._summary(reviewed)
    assert summary["conditional_reference"] == {"accept": 187, "hold": 9, "reject": 0}
    assert summary["source_disposition"] == {"keep": 187, "revise": 9}
    assert summary["benchmark_decision"] == {"hold": 196}
    assert summary["by_reference_grade"] == {
        "TS": {"accept": 36, "hold": 2},
        "S1": {"accept": 51, "hold": 5},
        "S2": {"accept": 49},
        "S3": {"accept": 51, "hold": 2},
    }
    assert (
        summary["new_documents"]
        == summary["training_released"]
        == summary["evaluation_released"]
        == 0
    )
    assert not summary["new_cv_performed"] and not summary["model_inference_performed"]


def test_common_independent_validator_accepts_prior196_schema(reviewed):
    from koipa.customer_reference_adoption_v1 import build_adoption_ledger

    ids = {r["doc_id"] for r in reviewed}
    parent = review.DEFAULT_PARENT
    docs = [
        r
        for r in review._rows(parent / "authoring/documents.jsonl")
        if r["input"]["doc_id"] in ids
    ]
    answers = [
        r
        for r in review._rows(parent / "answers/answers.candidate.jsonl")
        if r["doc_id"] in ids
    ]
    details = [
        r for r in review._rows(parent / "answers/evidence.jsonl") if r["doc_id"] in ids
    ]
    result = build_adoption_ledger(
        docs,
        answers,
        details,
        reviewed,
        source_manifest_sha256=review.SOURCE_MANIFEST_SHA256,
        hard_hold_benchmark_ids=[],
        expected_count=196,
        expected_hard_hold_count=0,
    )
    assert result["internal_reference_accepted"] == 187
    assert result["internal_reference_held"] == 9
    assert result["benchmark_held_candidates"] == 196
    assert (
        result["benchmark_training_adopted"]
        == result["benchmark_evaluation_adopted"]
        == 0
    )


def test_unresolved_findings_match_decisions(reviewed):
    originals = original_by_id()
    held = set()
    for row in reviewed:
        relevant = [
            f
            for f in row["findings"]
            if f["scope"] in ("conditional_reference", "both")
        ]
        assert any(f["scope"] == "benchmark" for f in row["findings"])
        if row["conditional_reference_decision"] == "hold":
            held.add(originals[row["doc_id"]]["family_id"].removeprefix("family-"))
            assert row["source_disposition"] == "revise" and len(relevant) == 1
            assert "산식" in row["decision_reason"]
        else:
            assert row["conditional_reference_decision"] == "accept" and not relevant
    assert held == set(review.FIXES)


@pytest.mark.parametrize(
    "key,quote,code",
    [
        ("membrane-backwash", "차이는 7L였다", "FLOW_DIFFERENCE_UNIT_NOT_EXPLICIT"),
        ("delivery-dedup", "고유 항목은 71개", "UNIQUE_CARDINALITY_PREMISE_MISSING"),
        (
            "timezone-migration",
            "변환 가능한 행은 63행",
            "CONVERTIBLE_COUNT_IGNORES_OTHER_AMBIGUITY",
        ),
        ("pallet-layout", "시험 적재 후", "ACQUIRED_STABILITY_SCOPE_NOT_LINKED"),
        ("service-bundle", "다음 달", "OBSERVED_COHORT_VERSUS_FUTURE_SCOPE_UNBOUND"),
    ],
)
def test_substantive_hold_evidence(reviewed, key, quote, code):
    originals = original_by_id()
    row = next(
        r for r in reviewed if originals[r["doc_id"]]["family_id"] == "family-" + key
    )
    assert row["conditional_reference_decision"] == "hold"
    assert any(quote in e["quote"] for e in row["body_evidence"])
    assert any(f["code"] == code.lower() for f in row["findings"])


def test_high_acquisition_cost_not_rejected_for_simple_arithmetic(reviewed):
    originals = original_by_id()
    tile = next(
        r
        for r in reviewed
        if originals[r["doc_id"]]["family_id"] == "family-tile-coordinate"
    )
    assert tile["conditional_reference_decision"] == "accept"
    assert (
        "다중축척" in tile["context_review_note"]
        and "단순빼기비" in tile["context_review_note"]
    )
    membrane = next(
        r
        for r in reviewed
        if originals[r["doc_id"]]["family_id"] == "family-membrane-backwash"
    )
    assert "시험비는 정합" in membrane["context_review_note"]
    assert membrane["findings"][-1]["code"] == "flow_difference_unit_not_explicit"


@pytest.mark.parametrize("which", ["empty", "missing", "duplicate", "unlisted"])
def test_authored_note_coverage_never_defaults_to_accept(monkeypatch, which):
    lines = review.REVIEW_NOTES.splitlines()
    if which == "empty":
        value = ""
    elif which == "missing":
        value = "\n".join(lines[1:])
    elif which == "duplicate":
        value = review.REVIEW_NOTES + "\n" + lines[0]
    else:
        parts = lines[0].split("|")
        parts[0] = "not-reviewed-family"
        lines[0] = "|".join(parts)
        value = "\n".join(lines)
    monkeypatch.setattr(review, "REVIEW_NOTES", value)
    with pytest.raises((FactContractError, ValueError)):
        review.compile_dispositions()


@pytest.mark.parametrize("quote", ["", "missing sentence", "same phrase twice"])
def test_invalid_evidence_is_not_invented(quote):
    with pytest.raises(FactContractError):
        review._evidence("same phrase twice / same phrase twice", quote)


def test_zero_jsonl_rejected(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("\n", encoding="utf-8")
    with pytest.raises(FactContractError):
        review._rows(path)


def test_original_source_pack_pin_rejected(tmp_path):
    path = tmp_path / "source"
    path.mkdir()
    (path / "manifest.json").write_text("{}")
    with pytest.raises(FactContractError, match="source_pin"):
        review.compile_dispositions(path)


@pytest.fixture
def fast_report(tmp_path, monkeypatch, reviewed):
    calls = []
    monkeypatch.setattr(
        review, "compile_dispositions", lambda parent_pack: copy.deepcopy(reviewed)
    )
    monkeypatch.setattr(
        review, "_verify_source", lambda parent_pack: calls.append(parent_pack)
    )
    monkeypatch.setattr(review, "_source_hashes", lambda: {"test-only.py": "a" * 64})
    return tmp_path / "result", calls


def test_report_roundtrip_and_no_overwrite(fast_report):
    out, calls = fast_report
    result = review.write_report(out)
    assert result == review.verify_report(out)
    assert len(calls) == 2
    snapshot = {p: p.read_bytes() for p in out.iterdir()}
    with pytest.raises(FactContractError):
        review.write_report(out)
    assert all(p.read_bytes() == content for p, content in snapshot.items())


@pytest.mark.parametrize(
    "which", ["record", "summary", "readme", "manifest", "extra", "rehash_wrong_grade"]
)
def test_report_tampering_rejected(fast_report, which):
    out, _ = fast_report
    review.write_report(out)
    if which == "extra":
        (out / "extra.txt").write_text("extra")
    elif which == "rehash_wrong_grade":
        path = out / "dispositions.jsonl"
        records = review._rows(path)
        records[0]["reference_grade"] = (
            "S3" if records[0]["reference_grade"] != "S3" else "TS"
        )
        path.write_text(
            "".join(
                json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"
                for r in records
            ),
            encoding="utf-8",
        )
        manifest = json.loads((out / "manifest.json").read_text())
        manifest["files"]["dispositions.jsonl"] = review._sha(path)
        (out / "manifest.json").write_text(json.dumps(manifest))
    else:
        name = {
            "record": "dispositions.jsonl",
            "summary": "summary.json",
            "readme": "REVIEW.md",
            "manifest": "manifest.json",
        }[which]
        path = out / name
        path.write_text("{}", encoding="utf-8")
    with pytest.raises((FactContractError, ValueError)):
        review.verify_report(out)


def test_late_source_drift_leaves_no_completion(fast_report, monkeypatch):
    out, calls = fast_report

    def hashes():
        return {"test-only.py": ("b" if len(calls) >= 2 else "a") * 64}

    monkeypatch.setattr(review, "_source_hashes", hashes)
    with pytest.raises(FactContractError, match="dependencies_changed_during_output"):
        review.write_report(out)
    assert (out / "dispositions.jsonl").exists()
    assert not (out / "manifest.json").exists()


def test_completion_written_only_after_last_recheck(fast_report, monkeypatch):
    out, calls = fast_report

    def check(parent_pack):
        calls.append(parent_pack)
        assert not (out / "manifest.json").exists()
        if len(calls) == 2:
            assert (out / "dispositions.jsonl").exists()

    monkeypatch.setattr(review, "_verify_source", check)
    review.write_report(out)
    assert (out / "manifest.json").exists()


def test_unrelated_frozen_ancestor_output_blocked(fast_report):
    out, _ = fast_report
    out.parent.joinpath("manifest.json").write_text("{}")
    with pytest.raises(FactContractError, match="frozen_ancestor"):
        review.write_report(out)
    assert not out.exists()


def test_cli_bad_source_fails_without_output(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "manifest.json").write_text("{}")
    out = tmp_path / "out"
    assert review.main(["write", "--parent-pack", str(source), "--out", str(out)]) == 2
    assert not out.exists()
