"""Synthetic submissions only; never publish pretend human review artifacts."""
from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "scripts"))

import classification_review_workflow as workflow
from prepare_content_reference import write_jsonl
from prepare_content_reference_review import record_sha

# Test-only IDs, not actual reviewer accounts. Files below exist only in pytest tmp_path.
ACTORS = {"R1": "10000000-0000-0000-0000-000000000001", "R2": "20000000-0000-0000-0000-000000000002"}
ADJUDICATOR = "30000000-0000-0000-0000-000000000003"


@pytest.fixture(scope="module")
def cases():
    return workflow.packet_cases()


def filled(case, slot, grade="S1", status="recommended"):
    row = workflow.submission_template(case, slot)
    row.update(state="submitted", reviewer_id=ACTORS[slot], actor_kind="human_declared",
               reviewed_at="2026-09-14T00:00:00+00:00", prior_answers_seen=False)
    rule = {"TS": "CP-TS-01", "S1": "CP-S1-01", "S2": "CP-S2-01", "S3": "CP-S3-01"}.get(grade)
    row["decision"] = {"status": status, "grade": grade, "rule_ids": [rule],
                       "evidence": [{"kind": "body", "start": 0, "end": 10, "quote": case["text"][:10]}],
                       "reason_codes": [], "rationale": "테스트 전용 구조 제출. 의미상 정답 주장이 아님.",
                       "not_higher_reason": "테스트 상위 경계 설명", "not_lower_reason": "테스트 하위 경계 설명",
                       "evidence_requests": []}
    if status != "recommended":
        row["decision"].update(grade=None, rule_ids=["CP-HOLD-01" if status == "needs_evidence" else "CP-HOLD-02"],
                               reason_codes=["evidence_insufficient" if status == "needs_evidence" else "policy_gap"],
                               evidence_requests=["테스트 추가 증거 요청"])
    return row


def submissions(cases, completed=1):
    return tuple([filled(case, slot) if i < completed else workflow.submission_template(case, slot)
                  for i, case in enumerate(cases)] for slot in workflow.SLOTS)


def test_all_blank_has_no_false_accuracy_or_human_results(cases):
    left, right = submissions(cases, 0)
    summary, results = workflow.compare(cases, left, right)
    assert summary["assigned_cases"] == 40
    assert summary["submitted_by_slot"] == {"R1": 0, "R2": 0}
    assert summary["paired_submissions"] == 0 and summary["decision_agreement_rate"] is None
    assert summary["queue_counts"] == {"awaiting_reviews": 40}
    assert all(r["final_grade"] is None and not r["gold_eligible"] for r in results)


def test_same_conclusion_does_not_become_a_gold_answer(cases):
    summary, rows = workflow.compare(cases, *submissions(cases))
    assert summary["self_declared_independent_pairs"] == 1 and summary["decision_agreement_rate"] == 1
    assert rows[0]["queue_status"] == "agreement_pending_adjudication"
    assert not summary["identity_authenticated"] and not summary["independence_verified"]
    assert not summary["gold_eligible"] and not summary["model_accuracy_measured"]


@pytest.mark.parametrize("change", ["same_actor", "prior_answers", "ai_assisted"])
def test_independence_issues_are_not_clean_pairs(cases, change):
    left, right = submissions(cases)
    if change == "same_actor":
        right[0]["reviewer_id"] = left[0]["reviewer_id"].upper()
    elif change == "prior_answers":
        right[0]["prior_answers_seen"] = True
    else:
        right[0]["actor_kind"] = "ai_assisted_declared"
    summary, rows = workflow.compare(cases, left, right)
    assert rows[0]["queue_status"] == "independence_check_required"
    assert summary["self_declared_independent_pairs"] == 0 and summary["decision_agreement_rate"] is None


def test_grade_vs_review_disagreement_and_high_vs_public(cases):
    left, right = submissions(cases, 2)
    right[0] = filled(cases[0], "R2", "S3")
    right[1] = filled(cases[1], "R2", status="needs_evidence")
    summary, rows = workflow.compare(cases, left, right)
    assert rows[0]["high_vs_public_disagreement"]
    assert rows[1]["queue_status"] == "decision_disagreement"
    assert summary["high_vs_public_disagreements"] == 1 and summary["decision_agreement_rate"] == 0


def test_rule_difference_not_hidden_by_same_grade(cases):
    left, right = submissions(cases)
    left[0] = filled(cases[0], "R1", "TS")
    right[0] = filled(cases[0], "R2", "TS")
    right[0]["decision"]["rule_ids"] = ["CP-TS-03"]
    _, rows = workflow.compare(cases, left, right)
    assert rows[0]["decision_agreement"] and not rows[0]["basis_agreement"]
    assert rows[0]["queue_status"] == "basis_disagreement"


def test_different_valid_evidence_is_a_flag_not_automatic_grade_disagreement(cases):
    left, right = submissions(cases)
    right[0]["decision"]["evidence"] = [{"kind": "context", "pointer": "/facts/scope_complete",
                                         "value_sha256": record_sha(cases[0]["context"]["facts"]["scope_complete"])}]
    _, rows = workflow.compare(cases, left, right)
    assert rows[0]["evidence_locations_differ"]
    assert rows[0]["queue_status"] == "agreement_pending_adjudication"


@pytest.mark.parametrize("mutation", ["duplicate", "missing_id", "binding", "policy", "slot", "latent", "approved",
                                     "invalid_account", "nil_account", "naive_time", "future", "mixed_actors",
                                     "span", "quote", "context_hash", "context_pointer", "grade_rule", "review_grade",
                                     "missing_request", "empty_reason", "missing_boundary", "unknown_reason", "empty_evidence"])
def test_invalid_submissions_fail_closed(cases, mutation):
    left, right = submissions(cases, 2)
    row = left[0]
    if mutation == "duplicate":
        left[-1] = row
    elif mutation == "missing_id":
        del row["case_id"]
    elif mutation == "binding":
        row["binding"] = row["binding"] | {"input_sha256": "0" * 64}
    elif mutation == "policy":
        row["binding"] = row["binding"] | {"policy_version": "old-policy"}
    elif mutation == "slot":
        row["reviewer_slot"] = "R2"
    elif mutation == "latent":
        row["state"] = "pending"
    elif mutation == "approved":
        row["approval_status"] = "approved"
    elif mutation == "invalid_account":
        row["reviewer_id"] = "R1"
    elif mutation == "nil_account":
        row["reviewer_id"] = "00000000-0000-0000-0000-000000000000"
    elif mutation == "naive_time":
        row["reviewed_at"] = "2026-09-14T00:00:00"
    elif mutation == "future":
        row["reviewed_at"] = "2099-01-01T00:00:00+00:00"
    elif mutation == "mixed_actors":
        row["reviewer_id"] = ADJUDICATOR
    elif mutation == "span":
        row["decision"]["evidence"][0]["start"] = -1
    elif mutation == "quote":
        row["decision"]["evidence"][0]["quote"] = "not in input"
    elif mutation in {"context_hash", "context_pointer"}:
        row["decision"]["evidence"] = [{"kind": "context", "pointer": "/facts/scope_complete" if mutation == "context_hash" else "/answer",
                                         "value_sha256": "0" * 64}]
    elif mutation == "grade_rule":
        row["decision"]["grade"] = "S3"
    elif mutation == "review_grade":
        row["decision"]["status"] = "needs_evidence"
    elif mutation in {"missing_request", "empty_reason"}:
        left[0] = filled(cases[0], "R1", status="needs_policy_review")
        left[0]["decision"]["evidence_requests" if mutation == "missing_request" else "reason_codes"] = []
    elif mutation == "missing_boundary":
        row["decision"]["not_higher_reason"] = None
    elif mutation == "unknown_reason":
        row["decision"]["reason_codes"] = ["confidence_is_high"]
    elif mutation == "empty_evidence":
        row["decision"]["evidence"] = []
    with pytest.raises(ValueError):
        workflow.compare(cases, left, right)


def recorded_resolution(cases, left, right):
    _, comparisons = workflow.compare(cases, left, right)
    proposals = [workflow.resolution_template(r) for r in comparisons]
    proposals[0].update(state="proposal_recorded", adjudicator_id=ADJUDICATOR,
                        recorded_at="2026-09-14T01:00:00+00:00", cause_codes=["agreement_checked"],
                        resolution_rationale="테스트 전용 조정 근거", decision=copy.deepcopy(left[0]["decision"]))
    return comparisons, proposals


def test_resolution_is_a_proposal_not_signature_or_final_grade(cases):
    left, right = submissions(cases)
    comparisons, proposals = recorded_resolution(cases, left, right)
    assessed = workflow.check_resolutions(proposals, comparisons, cases, left, right)
    assert assessed[0]["proposal_grade"] == "S1" and assessed[0]["final_grade"] is None
    assert assessed[0]["approval_status"] == "unapproved" and not assessed[0]["training_allowed"]


@pytest.mark.parametrize("mutation", ["stale_review", "same_actor", "early_time", "approval_field", "incomplete_pair", "no_cause"])
def test_bad_resolution_rejected(cases, mutation):
    left, right = submissions(cases)
    comparisons, proposals = recorded_resolution(cases, left, right)
    if mutation == "stale_review":
        proposals[0]["submission_hashes"] = {"R1": "0" * 64, "R2": "1" * 64}
    elif mutation == "same_actor":
        proposals[0]["adjudicator_id"] = ACTORS["R1"]
    elif mutation == "early_time":
        proposals[0]["recorded_at"] = "2026-09-13T00:00:00+00:00"
    elif mutation == "approval_field":
        proposals[0]["approved"] = True
    elif mutation == "incomplete_pair":
        right[0] = workflow.submission_template(cases[0], "R2")
        _, comparisons = workflow.compare(cases, left, right)
        proposals[0]["submission_hashes"] = comparisons[0]["submission_hashes"]
    else:
        proposals[0]["cause_codes"] = []
    with pytest.raises(ValueError):
        workflow.check_resolutions(proposals, comparisons, cases, left, right)


def test_disagreement_needs_actual_cause_not_agreement_only(cases):
    left, right = submissions(cases)
    right[0] = filled(cases[0], "R2", "S3")
    comparisons, proposals = recorded_resolution(cases, left, right)
    with pytest.raises(ValueError, match="Disagreement"):
        workflow.check_resolutions(proposals, comparisons, cases, left, right)
    proposals[0]["cause_codes"] = ["rule_interpretation"]
    assert workflow.check_resolutions(proposals, comparisons, cases, left, right)[0]["final_grade"] is None


def test_packet_has_no_answer_sidecar_and_roundtrips(tmp_path, cases):
    out = tmp_path / "packet"
    summary = workflow.prepare(out)
    assert summary["real_document_intake"] == 0
    assert workflow.load_packet(out) == cases
    for slot in workflow.SLOTS:
        target = out / "reviewers" / slot
        assert {p.name for p in target.iterdir()} == {"inputs.jsonl", "forms.template.jsonl", "POLICY.md", "README.md"}
        assert "expected_grade" not in (target / "inputs.jsonl").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="Output exists"):
        workflow.prepare(out)
    result = workflow.analyze(out, out / "reviewers/R1/forms.template.jsonl", out / "reviewers/R2/forms.template.jsonl", tmp_path / "blank_run")
    assert result["resolution_proposals"] == result["finalized_grades"] == 0
    # Test-only simulated submissions are isolated to pytest's temporary directory.
    left, right = submissions(cases)
    write_jsonl(tmp_path / "r1.jsonl", left)
    write_jsonl(tmp_path / "r2.jsonl", right)
    result = workflow.analyze(out, tmp_path / "r1.jsonl", tmp_path / "r2.jsonl", tmp_path / "test_run")
    assert result["paired_submissions"] == 1 and result["finalized_grades"] == 0
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert all(workflow.sha256(out / item["path"]) == item["sha256"] for item in manifest["files"])


def test_prepare_rejects_frozen_source_directory():
    with pytest.raises(ValueError, match="frozen"):
        workflow.prepare(workflow.SOURCE / "forbidden")
