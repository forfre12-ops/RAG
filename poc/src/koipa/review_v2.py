"""Offline review bindings, not a grade answer key, identity service or GOLD gate.

The coordinator may compare a submitted interpretation with the injected policy.
Disagreement is retained as a finding; it never rewrites the reviewer's answer.
"""
from __future__ import annotations

import copy
from typing import Any, Literal

from pydantic import Field, ValidationError

from koipa.evidence_collection import CollectionContext
from koipa.policy_facts import (
    FACT_TYPES, ContractModel, Digest, EvidenceRef, FactContractError, FactPacket,
    NonEmpty, _pointer, require, text_digest, timestamp, value_digest,
)
from koipa.policy_shadow import evaluate_shadow, parse_shadow_policy, policy_digest

Purpose = Literal["evidence_review", "policy_review", "gold_candidate_review"]
Grade = Literal["TS", "S1", "S2", "S3"]
SAFE_FLAGS = {
    "final_grade": None, "gold_eligible": False, "training_allowed": False,
    "model_evaluation_allowed": False, "identity_authenticated": False,
    "authorization_authenticated": False, "policy_approval_verified": False,
    "independence_verified": False, "customer_accuracy_measured": False,
    "automation_allowed": False, "finalized": False,
}


class ReferenceClaim(ContractModel):
    status: Literal["unknown", "provided"] = "unknown"
    reference: NonEmpty | None = None
    sha256: Digest | None = None


class UsagePermission(ContractModel):
    """Declared permit scope only; references are never opened or authenticated."""

    status: Literal["unknown", "provided", "denied"] = "unknown"
    org_id: NonEmpty
    reference: NonEmpty | None = None
    sha256: Digest | None = None
    purposes: list[Purpose] = Field(default_factory=list)
    case_ids: list[NonEmpty] = Field(default_factory=list)
    valid_from: NonEmpty | None = None
    valid_until: NonEmpty | None = None


class ReviewCase(ContractModel):
    case_id: NonEmpty
    document: CollectionContext
    family_id: NonEmpty
    source_origin: Literal["synthetic", "customer_real", "public_real"]
    partition: Literal["review_only", "development_candidate", "sealed_candidate"] = "review_only"
    input_view: Literal["body_only", "body_and_evidence", "evidence_only"]
    extraction_state: Literal["complete", "incomplete", "unknown"] = "unknown"
    extraction_receipt: ReferenceClaim = Field(default_factory=ReferenceClaim)
    collection_receipt: ReferenceClaim = Field(default_factory=ReferenceClaim)
    mapping_receipt: ReferenceClaim = Field(default_factory=ReferenceClaim)
    packet: FactPacket
    packet_sha256: Digest
    presented_source_ids: list[NonEmpty]


class ReviewAssignment(ContractModel):
    slot: NonEmpty
    case_ids: list[NonEmpty] = Field(min_length=1)
    assigned_at: NonEmpty
    identity_mode: Literal["unassigned", "self_declared", "upstream_claimed"] = "unassigned"
    reviewer_ref: NonEmpty | None = None


class ReviewManifest(ContractModel):
    schema_version: Literal["real-document-review-input-v2-draft"]
    job_id: NonEmpty
    org_id: NonEmpty
    created_at: NonEmpty
    purpose: Purpose
    policy_id: NonEmpty
    policy: dict[str, Any]
    policy_sha256: Digest
    org_receipt: ReferenceClaim = Field(default_factory=ReferenceClaim)
    policy_approval: ReferenceClaim = Field(default_factory=ReferenceClaim)
    authorization: UsagePermission
    cases: list[ReviewCase] = Field(min_length=1)
    assignments: list[ReviewAssignment] = Field(min_length=1)


class ReviewContext(ContractModel):
    """Independent caller expectation, not an authenticated organization scope."""

    org_id: NonEmpty
    job_id: NonEmpty
    manifest_sha256: Digest
    as_of: NonEmpty


class ReviewCitation(ContractModel):
    reference: EvidenceRef
    fact: NonEmpty | None = None


class ReviewDecision(ContractModel):
    status: Literal["candidate", "missing_evidence", "policy_conflict", "policy_gap"]
    grade: Grade | None
    rule_ids: list[NonEmpty]
    evidence: list[ReviewCitation]
    rationale: NonEmpty
    not_higher_reason: NonEmpty
    not_lower_reason: NonEmpty
    requests: list[NonEmpty]


class SubmissionRow(ContractModel):
    case_id: NonEmpty
    case_sha256: Digest
    policy_sha256: Digest
    packet_sha256: Digest
    state: Literal["pending", "submitted"] = "pending"
    reviewer_ref: NonEmpty | None = None
    actor_kind: Literal["human_declared", "ai_assisted_declared"] | None = None
    submitted_at: NonEmpty | None = None
    prior_answers_seen: bool | None = None
    decision: ReviewDecision | None = None


class SubmissionBatch(ContractModel):
    schema_version: Literal["real-document-review-submissions-v2-draft"]
    job_id: NonEmpty
    org_id: NonEmpty
    manifest_sha256: Digest
    slot: NonEmpty
    rows: list[SubmissionRow] = Field(min_length=1)


def _parse(model, raw):
    try:
        if isinstance(raw, ContractModel):
            raw = raw.model_dump(warnings=False)
        return model.model_validate(copy.deepcopy(raw))
    except (ValidationError, TypeError, AttributeError, ValueError):
        raise FactContractError("invalid_review_contract") from None


def _digest(model) -> str:
    return value_digest(model.model_dump(mode="json"))


def manifest_digest(raw) -> str:
    """Canonical validated-shape hash (cross-field validation is separate)."""
    return _digest(_parse(ReviewManifest, raw))


def _unique(values, code):
    require(len(values) == len(set(values)), code)


def _claim(claim):
    require((claim.reference is not None and claim.sha256 is not None)
            if claim.status == "provided" else (claim.reference is None and claim.sha256 is None),
            "review_reference_claim_inconsistent")


def _load(raw, context):
    manifest, context = _parse(ReviewManifest, raw), _parse(ReviewContext, context)
    require((manifest.org_id, manifest.job_id, _digest(manifest)) ==
            (context.org_id, context.job_id, context.manifest_sha256), "review_manifest_binding_mismatch")
    created, as_of = timestamp(manifest.created_at), timestamp(context.as_of)
    require(created <= as_of, "review_manifest_from_future")
    policy = parse_shadow_policy(manifest.policy)
    require(policy.org_id == manifest.org_id, "review_policy_org_mismatch")
    require(policy_digest(policy) == manifest.policy_sha256, "review_policy_hash_mismatch")
    require(set(policy.grade_order) == {"TS", "S1", "S2", "S3"}, "review_four_grades_required")
    _claim(manifest.org_receipt)
    _claim(manifest.policy_approval)
    cases = {case.case_id: case for case in manifest.cases}
    require(len(cases) == len(manifest.cases), "review_duplicate_case")
    document_keys = [(c.document.document_id, c.document.document_revision) for c in manifest.cases]
    _unique(document_keys, "review_duplicate_document_revision")
    families, contents, originals = {}, {}, {}
    checks = {}
    for case in manifest.cases:
        require(case.document.org_id == manifest.org_id, "review_document_org_mismatch")
        require(timestamp(case.document.as_of) <= created, "review_evidence_after_manifest")
        for claim in (case.extraction_receipt, case.collection_receipt, case.mapping_receipt):
            _claim(claim)
        for registry, key, code in (
            (families, case.family_id, "review_family_partition_overlap"),
            (contents, case.document.document_sha256, "review_body_partition_overlap"),
            (originals, case.document.original_sha256, "review_original_partition_overlap"),
        ):
            require(key not in registry or registry[key] == case.partition, code)
            registry[key] = case.partition
        require(_digest(case.packet) == case.packet_sha256, "review_packet_hash_mismatch")
        require(not case.packet.estimates, "review_estimates_not_allowed")
        expected_role = "synthetic_policy_fixture" if case.source_origin == "synthetic" else "unverified_supplied_assertions"
        require(case.packet.material_role == expected_role, "review_material_role_mismatch")
        _unique(case.presented_source_ids, "review_duplicate_presented_source")
        sources = case.packet.sources
        require(set(case.presented_source_ids) == {s.source_id for s in sources}, "review_unpresented_source")
        if case.input_view == "body_only":
            require(all(s.kind == "document_text" for s in sources), "review_body_only_contains_context")
        elif case.input_view == "evidence_only":
            require(all(s.kind != "document_text" for s in sources), "review_evidence_only_contains_body")
        # The existing evaluator binds every source/assertion and keeps unknown/conflict states.
        # The private result must never be copied into a blank reviewer worksheet.
        checks[case.case_id] = evaluate_shadow(policy, case.packet.model_dump(), context=case.document.fact_context())
    _unique([a.slot for a in manifest.assignments], "review_duplicate_slot")
    for assignment in manifest.assignments:
        _unique(assignment.case_ids, "review_duplicate_assignment_case")
        require(set(assignment.case_ids) <= cases.keys(), "review_unknown_assignment_case")
        require(created <= timestamp(assignment.assigned_at) <= as_of, "review_assignment_time_invalid")
        require((assignment.reviewer_ref is None) == (assignment.identity_mode == "unassigned"),
                "review_assignment_identity_inconsistent")
    permit = manifest.authorization
    require(permit.org_id == manifest.org_id, "review_permit_org_mismatch")
    _unique(permit.purposes, "review_duplicate_permit_purpose")
    _unique(permit.case_ids, "review_duplicate_permit_case")
    if permit.status == "provided":
        require(all(v is not None for v in (permit.reference, permit.sha256, permit.valid_from, permit.valid_until)),
                "review_permit_incomplete")
        require(manifest.purpose in permit.purposes and cases.keys() <= set(permit.case_ids), "review_permit_scope_mismatch")
        require(timestamp(permit.valid_from) <= created <= as_of < timestamp(permit.valid_until), "review_permit_time_invalid")
    else:
        require(not permit.purposes and not permit.case_ids and all(v is None for v in
                (permit.reference, permit.sha256, permit.valid_from, permit.valid_until)), "review_permit_status_inconsistent")
    return manifest, context, policy, cases, checks


def _manifest_report(manifest, context):
    missing = [name for name in ("org_receipt", "policy_approval") if getattr(manifest, name).status != "provided"]
    if manifest.authorization.status != "provided":
        missing.append("authorization_" + manifest.authorization.status)
    return {**SAFE_FLAGS, "schema_version": "real-document-review-check-v2-draft",
            "manifest_sha256": context.manifest_sha256, "policy_sha256": manifest.policy_sha256,
            "checked_as_of": context.as_of, "status": "needs_input_claims" if missing else "bound_draft_only",
            "missing_claims": missing, "case_count": len(manifest.cases), "assignment_count": len(manifest.assignments),
            "cases": [{"case_id": c.case_id, "case_sha256": _digest(c), "packet_sha256": c.packet_sha256,
                       "input_view": c.input_view, "source_count": len(c.packet.sources),
                       "findings": ([] if c.extraction_state == "complete" else ["extraction_" + c.extraction_state])}
                      for c in manifest.cases]}


def inspect_manifest(raw, *, context) -> dict:
    manifest, context, _, _, _ = _load(raw, context)
    return _manifest_report(manifest, context)


def _assignment(manifest, slot):
    matches = [a for a in manifest.assignments if a.slot == slot]
    require(len(matches) == 1, "review_unknown_slot")
    return matches[0]


def blank_submissions(raw, *, context, slot: str) -> dict:
    """Metadata-only worksheet: no answer, policy prediction, reviewer or signature."""
    manifest, context, _, cases, _ = _load(raw, context)
    assignment = _assignment(manifest, slot)
    require(manifest.authorization.status != "denied", "review_permission_denied")
    if any(cases[cid].source_origin != "synthetic" for cid in assignment.case_ids):
        require(manifest.authorization.status == "provided", "review_permission_claim_required")
    rows = [SubmissionRow(case_id=cid, case_sha256=_digest(cases[cid]), policy_sha256=manifest.policy_sha256,
                          packet_sha256=cases[cid].packet_sha256).model_dump() for cid in assignment.case_ids]
    return SubmissionBatch(schema_version="real-document-review-submissions-v2-draft", job_id=manifest.job_id,
                           org_id=manifest.org_id, manifest_sha256=context.manifest_sha256, slot=slot, rows=rows).model_dump()


def _citation(citation, case):
    ref = citation.reference
    source = next((s for s in case.packet.sources if s.source_id == ref.source_id), None)
    require(source is not None and ref.source_id in case.presented_source_ids, "review_citation_source_not_presented")
    require(ref.source_sha256 == source.payload_sha256, "review_citation_source_hash_mismatch")
    locator = ref.locator
    if locator.kind == "text_span":
        require(isinstance(source.payload, str), "review_citation_locator_type_mismatch")
        require(0 <= locator.start < locator.end <= len(source.payload), "review_citation_span_invalid")
        digest = text_digest(source.payload[locator.start:locator.end])
    else:
        require(isinstance(source.payload, dict), "review_citation_locator_type_mismatch")
        digest = value_digest(_pointer(source.payload, locator.pointer))
    require(ref.value_sha256 == digest, "review_citation_value_hash_mismatch")
    if citation.fact is not None:
        require(citation.fact in FACT_TYPES, "review_unknown_citation_fact")
        require(any(a.fact == citation.fact and ref in a.evidence for a in case.packet.facts),
                "review_citation_not_bound_to_fact")


def _decision(decision, case, policy, shadow):
    _unique(decision.rule_ids, "review_duplicate_cited_rule")
    _unique([_digest(c) for c in decision.evidence], "review_duplicate_citation")
    rules = {r.id: r for r in policy.rules}
    require(set(decision.rule_ids) <= rules.keys(), "review_unknown_rule")
    for citation in decision.evidence:
        _citation(citation, case)
    findings = []
    if decision.status == "candidate":
        require(decision.grade is not None and bool(decision.rule_ids) and bool(decision.evidence)
                and not decision.requests, "review_candidate_incomplete")
        require(all(rules[r].grade == decision.grade for r in decision.rule_ids), "review_rule_grade_mismatch")
        required_facts = {fact for r in decision.rule_ids for fact in (*rules[r].when, *rules[r].requires_evidence)}
        cited_facts = {a.fact for a in case.packet.facts
                       if any(c.reference in a.evidence for c in decision.evidence)}
        if not required_facts <= cited_facts:
            findings.append("required_rule_facts_not_cited")
        trace = {r["rule_id"]: r for r in shadow["rule_trace"]}
        if any(trace[r]["status"] != "matched" for r in decision.rule_ids):
            findings.append("cited_rule_not_supported_by_supplied_facts")
        if shadow["status"] != "candidate":
            findings.append("policy_blocks_candidate")
        elif decision.grade != shadow["grade"]:
            findings.append("submitted_grade_differs_from_policy_candidate")
    else:
        require(decision.grade is None and bool(decision.requests), "review_hold_incomplete")
        if decision.status == "policy_conflict":
            require(len(decision.rule_ids) >= 2, "review_conflict_requires_rules")
        findings.append(decision.status + "_claim_requires_review")
    return findings


def validate_submissions(raw, submitted, *, context) -> dict:
    manifest, context, policy, cases, checks = _load(raw, context)
    batch = _parse(SubmissionBatch, submitted)
    require((batch.org_id, batch.job_id, batch.manifest_sha256) ==
            (manifest.org_id, manifest.job_id, context.manifest_sha256), "review_submission_binding_mismatch")
    assignment = _assignment(manifest, batch.slot)
    _unique([r.case_id for r in batch.rows], "review_duplicate_submission_case")
    require({r.case_id for r in batch.rows} == set(assignment.case_ids), "review_submission_coverage_mismatch")
    rows, identities = [], set()
    for row in batch.rows:
        case = cases[row.case_id]
        require((row.case_sha256, row.policy_sha256, row.packet_sha256) ==
                (_digest(case), manifest.policy_sha256, case.packet_sha256), "review_submission_revision_mismatch")
        fields = (row.reviewer_ref, row.actor_kind, row.submitted_at, row.prior_answers_seen, row.decision)
        findings = [] if case.extraction_state == "complete" else ["extraction_" + case.extraction_state]
        if row.state == "pending":
            require(all(v is None for v in fields), "review_pending_contains_answer")
        else:
            require(all(v is not None for v in fields), "review_submission_incomplete")
            require(timestamp(assignment.assigned_at) <= timestamp(row.submitted_at) <= timestamp(context.as_of),
                    "review_submission_time_invalid")
            require(assignment.reviewer_ref is None or row.reviewer_ref == assignment.reviewer_ref,
                    "review_assigned_identity_mismatch")
            identities.add(row.reviewer_ref)
            if row.prior_answers_seen:
                findings.append("prior_answer_exposure_declared")
            if row.actor_kind == "ai_assisted_declared":
                findings.append("ai_assistance_declared")
            findings.extend(_decision(row.decision, case, policy, checks[row.case_id]))
        rows.append({"case_id": row.case_id, "state": row.state,
                     "decision_status": row.decision.status if row.decision else None, "findings": findings})
    require(len(identities) <= 1, "review_slot_multiple_identities")
    submitted_count = sum(r["state"] == "submitted" for r in rows)
    candidates = sum(r["decision_status"] == "candidate" for r in rows)
    report = _manifest_report(manifest, context)
    report.update(status="submissions_checked_review_required" if submitted_count == len(rows) else "awaiting_submissions",
                  submission_sha256=_digest(batch), rows=rows,
                  counts={"assigned": len(rows), "submitted": submitted_count, "pending": len(rows) - submitted_count,
                          "candidate": candidates, "hold": submitted_count - candidates,
                          "with_findings": sum(bool(r["findings"]) for r in rows)},
                  quality_accuracy=None, adjudication_performed=False)
    return report
