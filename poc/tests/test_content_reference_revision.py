"""Offline candidate-contract tests, not semantic or model quality certification."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "scripts"))

import prepare_content_reference_review as prior_review
import prepare_content_reference_revision as revision
from content_reference_contract import resolve
from content_reference_materials import BULK, CONTROL, CORE, run_toy, sensitive_table, unique_people, unique_people_across
from evaluation_inputs import sha256


@pytest.fixture(scope="module")
def sources():
    return revision.protected_sources()


@pytest.fixture(scope="module")
def built(sources):
    return revision.make_cases(sources[0], sources[1])


def pick(built, kind, grade="TS"):
    rows, answers, _ = copy.deepcopy(built)
    answer = next(a for a in answers if a["fixture"]["kind"] == kind and
                  a["views"][revision.VIEWS[1]]["expected_grade"] == grade)
    return next(r for r in rows if r["doc_id"] == answer["doc_id"]), answer


def test_forty_materials_and_separate_view_answers(sources, built):
    result = revision.validate(*built, sources[0], sources[1])
    assert result["material_cases"] == 40
    assert result["context_view_grade_counts"] == {"TS": 20, "S1": 20}
    assert result["body_only_grade_scored"] == 0
    assert result["toy_vector_checks"] == 36
    assert result["large_ledger_unique_people"] == 6000
    assert result["direct_evidence_spans"] == 70
    assert not result["customer_accuracy_measured"] and not result["training_allowed"]


@pytest.mark.parametrize("key", sorted(CORE))
def test_handwritten_toy_vectors(key):
    _, spec, vectors, _ = CORE[key]
    assert len(vectors) == 3
    for values, expected in vectors:
        assert run_toy(spec, values) == expected


@pytest.mark.parametrize("key", sorted(BULK))
def test_actual_large_and_small_person_rows(key):
    total, small, *_ = BULK[key]
    table = sensitive_table(key, total)
    assert unique_people(table) == len(table.splitlines()) - 1 == total
    assert unique_people(sensitive_table(key, small)) == small


@pytest.mark.parametrize("key", sorted(CONTROL))
def test_controls_are_fake_and_never_real_authentication(built, key):
    rows, answers, _ = built
    answer = next(a for a in answers if a["fixture"]["key"] == key and a["fixture"]["kind"] == "control")
    doc = next(r for r in rows if r["doc_id"] == answer["doc_id"])
    record = doc["context"]["facts"]["control_record"]["value"]
    assert "DEMO-ONLY-" in doc["text"] and record["real_system_validity"] is False
    assert revision.control_condition(doc) is True


@pytest.mark.parametrize("count,expected", [(999, "S1"), (1000, "TS"), (1001, "TS")])
def test_unique_person_threshold_not_row_count(built, count, expected):
    doc, answer = pick(built, "personal")
    answer["fixture"]["table"] = sensitive_table(answer["fixture"]["key"], count)
    # This tests the declared-observation oracle, not an arbitrary new document's validity.
    result = resolve(revision.fixture_facts(doc, answer["fixture"], revision.VIEWS[1]))
    assert result["grade"] == expected


def test_a_thousand_duplicate_rows_are_one_person():
    lines = sensitive_table("payroll", 1).splitlines()
    assert unique_people(lines[0] + "\n" + "\n".join([lines[1]] * 1000)) == 1


def test_joint_access_scope_counts_people_across_file_boundaries():
    lines = sensitive_table("payroll", 1000).splitlines()
    first = "\n".join(lines[:601])
    second = "\n".join([lines[0]] + lines[501:])
    assert unique_people(first) == 600 and unique_people(second) == 500
    assert unique_people_across([first, second]) == 1000
    assert unique_people_across([first, first]) == 600


def test_missing_joint_scope_is_not_zero_people():
    with pytest.raises(ValueError, match="Missing joint-scope"):
        unique_people_across([])


@pytest.mark.parametrize("kind,field", [("core", "reproduction_scope"), ("core", "distribution"),
                                       ("personal", "person_linkage"), ("control", "control_record")])
def test_missing_necessary_context_is_unknown_not_false(built, kind, field):
    doc, answer = pick(built, kind)
    doc["context"]["facts"][field]["state"] = "unknown"
    doc["context"]["facts"][field]["value"] = None
    result = revision.fixture_decision(doc, answer["fixture"], revision.VIEWS[1])
    assert result["grade"] is None and result["status"] == "needs_evidence"


@pytest.mark.parametrize("kind", ["core", "personal", "control", "partial"])
def test_body_only_never_borrow_hidden_context(built, kind):
    doc, answer = pick(built, kind, "S1" if kind == "partial" else "TS")
    assert revision.model_input(doc, "body_only") == doc["text"]
    assert revision.model_input(doc, revision.VIEWS[1]) != doc["text"]
    assert all(v is None for v in revision.fixture_facts(doc, answer["fixture"], "body_only").values())
    assert revision.fixture_decision(doc, answer["fixture"], "body_only")["grade"] is None


@pytest.mark.parametrize("field,value", [("revoked", True), ("additional_factor_required", True),
                                       ("all_required_material_supplied", False),
                                       ("valid_until", "2026-09-14T00:00:00+00:00"),
                                       ("asset", "wrong-target"), ("permission", "wrong-permission"),
                                       ("material_sha256", "0" * 64)])
def test_control_disqualifiers_cannot_stay_ts(built, field, value):
    doc, answer = pick(built, "control")
    doc["context"]["facts"]["control_record"]["value"][field] = value
    assert revision.control_condition(doc) is False
    assert revision.fixture_decision(doc, answer["fixture"], revision.VIEWS[1])["grade"] != "TS"


def test_ambiguous_time_or_incomplete_control_record_is_not_expired(built):
    doc, _ = pick(built, "control")
    doc["context"]["facts"]["control_record"]["value"]["valid_until"] = "unknown"
    assert revision.control_condition(doc) is None
    del doc["context"]["facts"]["control_record"]["value"]["asset"]
    assert revision.control_condition(doc) is None


@pytest.mark.parametrize("reason", ["context_conflict", "policy_gap"])
def test_conflict_and_policy_gap_remain_distinct_review_reasons(built, reason):
    doc, answer = pick(built, "core")
    result = revision.fixture_decision(doc, answer["fixture"], revision.VIEWS[1], unresolved_reason=reason)
    assert result["grade"] is None and result["status"] == "needs_policy_review"
    assert result["reason_code"] == reason and not result["candidate_bounds_are_absolute"]


def test_incomplete_scope_does_not_invent_upper_grade_absence(built):
    doc, answer = pick(built, "partial", "S1")
    doc["context"]["facts"]["scope_complete"]["state"] = "unknown"
    doc["context"]["facts"]["scope_complete"]["value"] = None
    assert revision.fixture_decision(doc, answer["fixture"], revision.VIEWS[1])["grade"] is None
    doc, answer = pick(built, "core")
    doc["context"]["facts"]["scope_complete"]["state"] = "unknown"
    doc["context"]["facts"]["scope_complete"]["value"] = None
    assert revision.fixture_decision(doc, answer["fixture"], revision.VIEWS[1])["grade"] == "TS"


@pytest.mark.parametrize("mutation", ["body", "body_hash", "context", "context_hash", "policy", "family", "parent",
                                     "signature", "gold", "training", "approved", "view_hash", "grade",
                                     "span", "quote", "context_evidence", "rule_evidence", "label_leak", "source", "lineage"])
def test_corrupt_pack_rejected(sources, built, mutation):
    rows, answers, links = copy.deepcopy(built)
    row, answer = rows[0], answers[0]
    if mutation == "body":
        row["text"] += " extra"
    elif mutation == "body_hash":
        row["text_sha256"] = "0" * 64
    elif mutation == "context":
        row["context"]["world"] = "real"
    elif mutation == "context_hash":
        row["context_sha256"] = "0" * 64
    elif mutation == "policy":
        answer["policy_version"] = "another"
    elif mutation == "family":
        answer["family_id"] = "another"
    elif mutation == "parent":
        answer["parent_answer_sha256"] = "0" * 64
    elif mutation == "signature":
        answer["human_signature"] = "forged"
    elif mutation == "gold":
        answer["gold_eligible"] = True
    elif mutation == "training":
        answer["training_allowed"] = True
    elif mutation == "approved":
        answer["approval_status"] = "approved"
    elif mutation == "view_hash":
        answer["views"]["body_only"]["input_sha256"] = answer["views"][revision.VIEWS[1]]["input_sha256"]
    elif mutation == "grade":
        answer["views"]["body_only"]["expected_grade"] = "TS"
    elif mutation == "span":
        answer["body_evidence"][0]["start"] = -1
    elif mutation == "quote":
        answer["body_evidence"][0]["quote"] = "missing"
    elif mutation == "context_evidence":
        answer["context_evidence"][0]["value_sha256"] = "0" * 64
    elif mutation == "rule_evidence":
        answer["rule_evidence_ids"] = {}
    elif mutation == "label_leak":
        row["expected_grade"] = "TS"
    elif mutation == "source":
        answer["context_evidence"][0]["origin"] = "verified_context"
    elif mutation == "lineage":
        links[0]["split"] = "sealed_candidate"
    with pytest.raises(ValueError):
        revision.validate(rows, answers, links, sources[0], sources[1])


def test_missing_or_duplicate_case_fails(sources, built):
    rows, answers, links = copy.deepcopy(built)
    rows[-1] = rows[0]
    with pytest.raises(ValueError, match="IDs"):
        revision.validate(rows, answers, links, sources[0], sources[1])


def test_review_ten_are_not_forced_into_grades_and_kept_forty_are_not_real_gold(sources):
    impacts = revision.impact_rows(sources[0], sources[1])
    review = [r for r in impacts if r["disposition"] == "keep_review_pending"]
    kept = [r for r in impacts if r["disposition"] == "retain_as_scenario_only"]
    assert len(review) == 10 and len(kept) == 40
    assert all(r["candidate_grade"] is None for r in review)
    assert all(r["request"] and r["resolution_owner_role"] and r["closure_condition"] for r in review)
    assert all(r["original_label_replacement"] is None and not r["unchanged_text_model_answer_adjudicated"] for r in impacts)


def test_legacy_four_keep_both_original_labels_and_three_abstentions(sources):
    rows = revision.legacy_followups(sources[2])
    assert len(rows) == 4 and sum(r["proposal"]["grade"] == "S1" for r in rows) == 1
    assert sum(r["proposal"]["grade"] is None for r in rows) == 3
    assert all(len(r["source_sides"]) == 2 and r["original_label_replacement"] is None for r in rows)
    assert all(not r["historical_policy_version_verified"] for r in rows)


def test_no_sealed_body_parsing(monkeypatch):
    reader = prior_review.read_rows
    reads = []
    def guarded(path):
        assert "sealed_candidate" not in str(path)
        reads.append(str(path))
        return reader(path)
    monkeypatch.setattr(prior_review, "read_rows", guarded)
    sources = revision.protected_sources()
    assert reads and any("sealed_candidate" in str(p) for p in sources[3])


@pytest.mark.parametrize("frozen", [revision.PACK, revision.REVIEW])
def test_output_cannot_be_inside_frozen_material(frozen):
    with pytest.raises(ValueError, match="frozen"):
        revision.build(frozen / "do-not-create")


def test_saved_pack_roundtrip_no_overwrite_and_preservation(tmp_path, sources):
    out = tmp_path / "revision"
    summary = revision.build(out)
    assert revision.check_saved_pack(out) == summary
    assert all(sha256(p) == digest for p, digest in sources[3].items())
    assert len(list((out / "documents").glob("*.md"))) == 40
    assert len(list((out / "candidates").glob("*.md"))) == 40
    with pytest.raises(ValueError, match="Output exists"):
        revision.build(out)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert all(sha256(out / item["path"]) == item["sha256"] for item in manifest["files"])


def test_unknown_toy_opcode_not_executed():
    with pytest.raises(ValueError, match="Unknown toy"):
        run_toy({"op": "__import__('os').system"}, [1])


def test_known_pool_exact_overlap_separately_reported(built):
    results = revision.known_overlap(built[0])
    assert len(results) == 4 and sum(r["rows"] for r in results) == 3164
    assert all(r["normalized_body_overlap"] == 0 and not r["semantic_family_overlap_verified"] for r in results)
