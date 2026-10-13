"""Offline pair comparison and hash-bound adjudication proposals; never GOLD.

Detected differences are not diagnosed root causes. Human-supplied explanations
are claims, and no agreement/proposal authenticates people or finalizes grades.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field

from koipa.policy_facts import ContractModel, Digest, NonEmpty, require, timestamp, value_digest
from koipa.review_v2 import (
    SAFE_FLAGS, ReviewDecision, SubmissionBatch, _decision, _digest, _load, _parse,
    _unique, validate_submissions,
)


class PairBinding(ContractModel):
    schema_version: Literal["real-document-review-pair-binding-v2-draft"]
    org_id: NonEmpty
    job_id: NonEmpty
    manifest_sha256: Digest
    left_slot: NonEmpty
    right_slot: NonEmpty
    left_submission_sha256: Digest
    right_submission_sha256: Digest


class IssueResponse(ContractModel):
    issue_code: NonEmpty
    action: Literal["retain_for_review", "request_new_input", "request_policy_change", "interpretation_proposed"]
    reason: NonEmpty


CauseCode = Literal[
    "extraction_gap", "management_evidence_gap", "mapping_dispute", "policy_gap", "policy_conflict",
    "rule_interpretation", "label_error", "citation_difference", "explanation_difference",
    "independence_issue", "agreement_check", "undetermined",
]


class AdjudicationRow(ContractModel):
    case_id: NonEmpty
    case_sha256: Digest
    left_row_sha256: Digest
    right_row_sha256: Digest
    state: Literal["pending", "proposed"] = "pending"
    adjudicator_ref: NonEmpty | None = None
    actor_kind: Literal["human_declared", "ai_assisted_declared"] | None = None
    proposed_at: NonEmpty | None = None
    reviewed_both: bool | None = None
    cause_codes: list[CauseCode] = Field(default_factory=list)
    issue_responses: list[IssueResponse] = Field(default_factory=list)
    resolution_note: NonEmpty | None = None
    decision: ReviewDecision | None = None


class AdjudicationBatch(ContractModel):
    schema_version: Literal["real-document-review-adjudication-v2-draft"]
    binding: PairBinding
    pair_sha256: Digest
    rows: list[AdjudicationRow] = Field(min_length=1)


def _set_digest(items):
    return {value_digest(item.model_dump() if isinstance(item, ContractModel) else item) for item in items}


def _differences(left, right):
    differences = []
    for field in ("status", "grade", "rationale", "not_higher_reason", "not_lower_reason"):
        if getattr(left, field) != getattr(right, field):
            differences.append(field)
    for field in ("rule_ids", "evidence", "requests"):
        if _set_digest(getattr(left, field)) != _set_digest(getattr(right, field)):
            differences.append(field)
    return sorted(differences)


def _load_pair(raw, left_raw, right_raw, context):
    manifest, context, policy, cases, checks = _load(raw, context)
    left, right = _parse(SubmissionBatch, left_raw), _parse(SubmissionBatch, right_raw)
    left_report = validate_submissions(manifest, left, context=context)
    right_report = validate_submissions(manifest, right, context=context)
    require(left.slot != right.slot, "review_pair_distinct_slots_required")
    left_rows, right_rows = ({r.case_id: r for r in batch.rows} for batch in (left, right))
    # Never silently intersect assignments and make unpaired cases disappear from a denominator.
    require(left_rows.keys() == right_rows.keys(), "review_pair_assignment_coverage_mismatch")
    binding = PairBinding(schema_version="real-document-review-pair-binding-v2-draft", org_id=manifest.org_id,
                          job_id=manifest.job_id, manifest_sha256=context.manifest_sha256,
                          left_slot=left.slot, right_slot=right.slot,
                          left_submission_sha256=_digest(left), right_submission_sha256=_digest(right))
    reports = [{r["case_id"]: r for r in report["rows"]} for report in (left_report, right_report)]
    rows = []
    for cid in sorted(left_rows):
        a, b = left_rows[cid], right_rows[cid]
        complete = a.state == b.state == "submitted"
        same_actor = bool(complete and a.reviewer_ref == b.reviewer_ref)
        exclusions = []
        if not complete:
            exclusions.append("pending_submission")
        if same_actor:
            exclusions.append("same_reviewer_claim")
        for side, row in (("left", a), ("right", b)):
            if row.prior_answers_seen:
                exclusions.append(side + ":prior_answer_exposure_declared")
            if row.actor_kind == "ai_assisted_declared":
                exclusions.append(side + ":ai_assistance_declared")
        differences = _differences(a.decision, b.decision) if complete else []
        issues = ["difference:" + d for d in differences]
        for side, report in zip(("left", "right"), reports, strict=True):
            issues.extend(side + ":" + finding for finding in report[cid]["findings"])
        if same_actor:
            issues.append("same_reviewer_claim")
        outcome_agrees = (a.decision.status, a.decision.grade) == (b.decision.status, b.decision.grade) if complete else None
        candidate_pair = complete and a.decision.status == b.decision.status == "candidate"
        rows.append({"case_id": cid, "case_sha256": _digest(cases[cid]), "left_row_sha256": _digest(a),
                     "right_row_sha256": _digest(b), "state": "compared_review_required" if complete else "awaiting_pair",
                     "left_state": a.state, "right_state": b.state, "complete": complete,
                     "outcome_agrees": outcome_agrees, "candidate_pair": candidate_pair,
                     "grade_agrees": a.decision.grade == b.decision.grade if candidate_pair else None,
                     "rule_set_agrees": "rule_ids" not in differences if complete else None,
                     "evidence_set_agrees": "evidence" not in differences if complete else None,
                     "differences": differences, "issue_codes": sorted(set(issues)),
                     "declared_clean_pair": complete and not exclusions, "exclusion_reasons": exclusions})
    return manifest, context, policy, cases, checks, binding, left_rows, right_rows, rows, left_report["missing_claims"]


def _rate(numerator, denominator):
    return {"numerator": numerator, "denominator": denominator, "rate": numerator / denominator if denominator else None}


def _metrics(rows):
    complete = [r for r in rows if r["complete"]]
    candidates = [r for r in complete if r["candidate_pair"]]
    return {"completed_pairs": len(complete), "candidate_pairs": len(candidates),
            "outcome_agreement": _rate(sum(r["outcome_agrees"] for r in complete), len(complete)),
            "grade_agreement": _rate(sum(r["grade_agrees"] for r in candidates), len(candidates)),
            "rule_set_agreement": _rate(sum(r["rule_set_agrees"] for r in complete), len(complete)),
            "evidence_set_agreement": _rate(sum(r["evidence_set_agrees"] for r in complete), len(complete))}


def _report(loaded):
    _, context, _, _, _, binding, _, _, rows, missing = loaded
    complete = sum(r["complete"] for r in rows)
    return {**SAFE_FLAGS, "schema_version": "real-document-review-pair-check-v2-draft",
            "status": "pair_compared_review_required" if complete == len(rows) else "awaiting_pairs",
            "binding": binding.model_dump(), "pair_sha256": _digest(binding), "checked_as_of": context.as_of,
            "missing_claims": missing, "rows": rows,
            "counts": {"assigned_pairs": len(rows), "completed_pairs": complete, "pending_pairs": len(rows) - complete,
                       "pairs_with_issues": sum(bool(r["issue_codes"]) for r in rows),
                       "declared_clean_pairs": sum(r["declared_clean_pair"] for r in rows)},
            "agreement_all_completed": _metrics(rows),
            "agreement_declared_clean_only": _metrics([r for r in rows if r["declared_clean_pair"]]),
            "quality_accuracy": None, "root_causes_verified": False, "adjudication_performed": False}


def compare_submissions(raw, left, right, *, context) -> dict:
    """Coordinator-only report. Contains agreement statistics, not an answer key."""
    return _report(_load_pair(raw, left, right, context))


def blank_adjudications(raw, left, right, *, context) -> dict:
    loaded = _load_pair(raw, left, right, context)
    manifest, _, _, cases, _, binding, _, _, rows, _ = loaded
    require(manifest.authorization.status != "denied", "review_permission_denied")
    if any(cases[r["case_id"]].source_origin != "synthetic" for r in rows):
        require(manifest.authorization.status == "provided", "review_permission_claim_required")
    blanks = [AdjudicationRow(**{k: row[k] for k in
              ("case_id", "case_sha256", "left_row_sha256", "right_row_sha256")}).model_dump() for row in rows]
    return AdjudicationBatch(schema_version="real-document-review-adjudication-v2-draft", binding=binding,
                              pair_sha256=_digest(binding), rows=blanks).model_dump()


def validate_adjudications(raw, left, right, adjudications, *, context) -> dict:
    loaded = _load_pair(raw, left, right, context)
    _, context, policy, cases, checks, binding, left_rows, right_rows, pairs, _ = loaded
    batch = _parse(AdjudicationBatch, adjudications)
    require(batch.binding == binding and batch.pair_sha256 == _digest(binding), "review_adjudication_pair_binding_mismatch")
    _unique([r.case_id for r in batch.rows], "review_duplicate_adjudication_case")
    require({r.case_id for r in batch.rows} == set(left_rows), "review_adjudication_coverage_mismatch")
    by_case = {r["case_id"]: r for r in pairs}
    result_rows, identities = [], set()
    for row in sorted(batch.rows, key=lambda r: r.case_id):
        pair, a, b = by_case[row.case_id], left_rows[row.case_id], right_rows[row.case_id]
        require(all(getattr(row, key) == pair[key] for key in ("case_sha256", "left_row_sha256", "right_row_sha256")),
                "review_adjudication_row_binding_mismatch")
        fields = (row.adjudicator_ref, row.actor_kind, row.proposed_at, row.reviewed_both, row.resolution_note, row.decision)
        findings = []
        if row.state == "pending":
            require(all(v is None for v in fields) and not row.cause_codes and not row.issue_responses,
                    "review_pending_adjudication_contains_proposal")
        else:
            require(pair["complete"], "review_adjudication_needs_both_submissions")
            require(all(v is not None for v in fields) and bool(row.cause_codes) and row.reviewed_both is True,
                    "review_adjudication_incomplete")
            require(row.adjudicator_ref not in {a.reviewer_ref, b.reviewer_ref}, "review_adjudicator_matches_reviewer")
            identities.add(row.adjudicator_ref)
            require(max(timestamp(a.submitted_at), timestamp(b.submitted_at)) <= timestamp(row.proposed_at)
                    <= timestamp(context.as_of), "review_adjudication_time_invalid")
            _unique(row.cause_codes, "review_duplicate_cause_code")
            _unique([r.issue_code for r in row.issue_responses], "review_duplicate_issue_response")
            require({r.issue_code for r in row.issue_responses} == set(pair["issue_codes"]),
                    "review_issue_response_coverage_mismatch")
            if any(r.action in {"request_new_input", "request_policy_change"} for r in row.issue_responses):
                require(row.decision.status != "candidate", "review_pending_change_cannot_propose_grade")
            findings.extend(_decision(row.decision, cases[row.case_id], policy, checks[row.case_id]))
            if row.actor_kind == "ai_assisted_declared":
                findings.append("adjudication_ai_assistance_declared")
        result_rows.append({"case_id": row.case_id, "state": row.state,
                            "decision_status": row.decision.status if row.decision else None,
                            "cause_codes_claimed": list(row.cause_codes), "issue_responses_count": len(row.issue_responses),
                            "proposal_findings": findings, "original_issue_codes": list(pair["issue_codes"])})
    require(len(identities) <= 1, "review_adjudication_multiple_identities")
    report = _report(loaded)
    proposed = sum(r["state"] == "proposed" for r in result_rows)
    report.update(status="adjudication_proposals_checked_review_required" if proposed == len(result_rows)
                  else "awaiting_adjudication_proposals", adjudication_batch_sha256=_digest(batch),
                  adjudication_rows=result_rows, proposed_count=proposed, pending_proposal_count=len(result_rows) - proposed,
                  # Checked proposals must not be mistaken for completed authoritative adjudication.
                  adjudication_proposals_checked=True, adjudication_performed=False)
    return report
