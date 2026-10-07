"""Version-bound customer vocabulary previews; never export observed policy facts.

No I/O, approval, alias inference, grade mapping or production integration.
An input declaration of approval is not an authenticated approval decision.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import Field, ValidationError

from koipa.evidence_collection import ACCESS_SCOPES, MARKINGS, CollectionContext, DocumentBinding
from koipa.policy_facts import (
    ContractModel, Digest, EvidenceRef, FactContractError, JsonPointer, NonEmpty,
    _pointer, require, timestamp, value_digest,
)

MappingFact = Literal["security_marking", "access_scope"]
FACTS = ("security_marking", "access_scope")


class MappingReference(ContractModel):
    source_id: NonEmpty
    org_id: NonEmpty
    source_ref: NonEmpty
    captured_at: NonEmpty
    payload: dict[str, Any]
    payload_sha256: Digest


class MappingRule(ContractModel):
    rule_id: NonEmpty
    fact: MappingFact
    raw_value: NonEmpty
    canonical_value: NonEmpty
    evidence: list[EvidenceRef] = Field(min_length=1)


class MappingPackage(ContractModel):
    schema_version: Literal["customer-vocabulary-mapping-v1-draft"]
    org_id: NonEmpty
    mapping_id: NonEmpty
    version: NonEmpty
    effective_at: NonEmpty
    valid_until: NonEmpty | None = None
    declared_status: Literal["draft", "review_requested", "approved", "retired"] = "draft"
    # Opaque reference supplied by a caller, never followed or authenticated here.
    approval_record_ref: NonEmpty | None = None
    sources: list[MappingReference]
    rules: list[MappingRule]


class RawField(ContractModel):
    state: Literal["observed", "proven_absent", "unknown"]
    pointer: NonEmpty | None


class RawManagementSnapshot(DocumentBinding):
    snapshot_id: NonEmpty
    provider_ref: NonEmpty
    origin: Literal["system_export", "request_metadata", "unverified_stored_metadata"]
    captured_at: NonEmpty
    valid_until: NonEmpty | None = None
    payload: dict[str, Any]
    payload_sha256: Digest
    fields: dict[MappingFact, RawField]


class MappingContext(ContractModel):
    document: CollectionContext
    mapping_id: NonEmpty
    mapping_version: NonEmpty
    mapping_sha256: Digest


@dataclass(frozen=True)
class MappingPreview:
    # Contains raw values/references. Store only with an explicit sensitive export.
    artifact: dict[str, Any]
    report: dict[str, Any]


def _parse(model, raw):
    try:
        return model.model_validate(copy.deepcopy(raw))
    except (ValidationError, AttributeError, TypeError):
        raise FactContractError("invalid_mapping_contract") from None


def mapping_digest(raw: dict | MappingPackage) -> str:
    """Hash parsed package including sources and lifecycle, not JSON file bytes."""
    return value_digest(_parse(MappingPackage, raw).model_dump())


def _json_only(value: Any) -> None:
    if type(value) is dict:
        require(all(type(key) is str for key in value), "non_json_mapping_payload")
        for child in value.values():
            _json_only(child)
    elif type(value) is list:
        for child in value:
            _json_only(child)
    else:
        require(value is None or type(value) in {str, bool, int, float}, "non_json_mapping_payload")


def _check_payload(payload: dict, digest: str) -> None:
    _json_only(payload)
    require(value_digest(payload) == digest, "mapping_payload_hash_mismatch")


def _validate_package(package: MappingPackage, context: MappingContext) -> str | None:
    require(package.org_id == context.document.org_id, "mapping_org_mismatch")
    require((package.mapping_id, package.version, mapping_digest(package))
            == (context.mapping_id, context.mapping_version, context.mapping_sha256), "mapping_version_binding_mismatch")
    require(package.declared_status != "approved" or package.approval_record_ref is not None,
            "declared_approval_reference_missing")
    require(package.declared_status in {"approved", "retired"} or package.approval_record_ref is None,
            "unexpected_approval_reference")
    as_of, effective = timestamp(context.document.as_of), timestamp(package.effective_at)
    until = timestamp(package.valid_until) if package.valid_until is not None else None
    require(until is None or effective < until, "invalid_mapping_effective_window")
    sources = {s.source_id: s for s in package.sources}
    require(len(sources) == len(package.sources), "duplicate_mapping_source")
    require(len({r.rule_id for r in package.rules}) == len(package.rules), "duplicate_mapping_rule")
    for source in sources.values():
        require(source.org_id == package.org_id, "mapping_reference_org_mismatch")
        require(timestamp(source.captured_at) <= as_of, "mapping_reference_from_future")
        _check_payload(source.payload, source.payload_sha256)
    for rule in package.rules:
        vocabulary = MARKINGS if rule.fact == "security_marking" else ACCESS_SCOPES
        require(rule.canonical_value in vocabulary, "invalid_mapping_canonical_value")
        record = {"fact": rule.fact, "raw_value": rule.raw_value, "canonical_value": rule.canonical_value}
        expected = value_digest(record)
        for ref in rule.evidence:
            require(ref.source_id in sources, "mapping_reference_missing")
            source = sources[ref.source_id]
            require(ref.source_sha256 == source.payload_sha256, "mapping_reference_hash_mismatch")
            require(isinstance(ref.locator, JsonPointer), "mapping_record_pointer_required")
            # Reuse the existing strict JSON Pointer resolver (including ~0/~1).
            require(value_digest(_pointer(source.payload, ref.locator.pointer)) == expected,
                    "mapping_rule_evidence_mismatch")
            require(ref.value_sha256 == expected, "mapping_record_hash_mismatch")
    if package.declared_status == "retired":
        return "mapping_retired"
    if effective > as_of:
        return "mapping_not_effective"
    if until is not None and as_of >= until:
        return "mapping_expired"
    return None


def _validate_snapshot(snapshot: RawManagementSnapshot, context: MappingContext) -> dict:
    require(all(getattr(snapshot, key) == getattr(context.document, key)
                for key in DocumentBinding.model_fields), "mapping_document_binding_mismatch")
    captured, as_of = timestamp(snapshot.captured_at), timestamp(context.document.as_of)
    require(captured <= as_of, "raw_snapshot_from_future")
    if snapshot.valid_until is not None:
        until = timestamp(snapshot.valid_until)
        require(captured <= until and as_of < until, "raw_snapshot_expired_or_invalid_window")
    _check_payload(snapshot.payload, snapshot.payload_sha256)
    values = {}
    for fact, field in snapshot.fields.items():
        if field.state == "unknown":
            require(field.pointer is None, "unknown_raw_field_has_pointer")
            continue
        require(field.pointer is not None, "raw_field_pointer_required")
        value = _pointer(snapshot.payload, field.pointer)
        if field.state == "proven_absent":
            require(value is None, "raw_absence_requires_explicit_null")
        else:
            require(type(value) is str and bool(value.strip()), "raw_observation_requires_string")
        values[fact] = value
    return values


def preview_mapping(raw_package: dict, raw_snapshot: dict, *, context: MappingContext) -> MappingPreview:
    """Deterministic proposed conversions with replayable provenance, no facts export."""
    package = _parse(MappingPackage, raw_package)
    snapshot = _parse(RawManagementSnapshot, raw_snapshot)
    context = _parse(MappingContext, context)
    blocked = _validate_package(package, context)
    raw_values = _validate_snapshot(snapshot, context)
    snapshot_sha = value_digest(snapshot.model_dump())
    proposals = []
    for fact in FACTS:
        field = snapshot.fields.get(fact)
        value = raw_values.get(fact)
        evidence = None if fact not in raw_values else {
            "snapshot_sha256": snapshot_sha, "payload_sha256": snapshot.payload_sha256,
            "pointer": field.pointer, "value_sha256": value_digest(value),
        }
        matches = []
        canonical = None
        if fact not in raw_values:
            status = "unknown"
        elif field.state == "proven_absent":
            status = "absence_claim_preserved"
        elif blocked is not None:
            status = blocked
        else:
            # Exact equality only. No trim, case-fold, keyword, vector or LLM fallbacks.
            matches = sorted((rule for rule in package.rules if rule.fact == fact and rule.raw_value == value),
                             key=lambda rule: rule.rule_id)
            outputs = {rule.canonical_value for rule in matches}
            status = "unmapped" if not outputs else "conflict" if len(outputs) > 1 else "mapped_candidate"
            if status == "mapped_candidate":
                canonical = next(iter(outputs))
        proposals.append({"fact": fact, "status": status, "raw_state": field.state if field else "unknown",
                          "raw_value": value, "canonical_candidate": canonical, "raw_evidence": evidence,
                          "matched_rules": [{"rule_id": r.rule_id, "canonical_candidate": r.canonical_value,
                                             "evidence": [ref.model_dump() for ref in r.evidence]} for r in matches]})
    # Report package-wide conflicts too; an unrelated successful preview must not
    # be presented as validation that the whole mapping is publishable.
    selectors = {}
    for rule in package.rules:
        selectors.setdefault((rule.fact, rule.raw_value), set()).add(rule.canonical_value)
    conflict_count = sum(len(outputs) > 1 for outputs in selectors.values())
    flags = {"approval_authenticity_verified": False, "source_authenticity_verified": False,
             "semantic_truth_verified": False, "organization_identity_authenticated": False,
             "fact_export_allowed": False, "management_snapshot_export_allowed": False,
             "training_allowed": False, "model_evaluation_allowed": False,
             "automation_allowed": False, "customer_accuracy_measured": False, "finalized": False}
    report = {
        "schema_version": "customer-mapping-preview-report-v1-draft", "mode": "proposal_only",
        "status": "blocked_mapping" if blocked else "preview_requires_review",
        "mapping_block_reason": blocked, "mapping_sha256": mapping_digest(package),
        "raw_snapshot_sha256": snapshot_sha, "context_sha256": value_digest(context.model_dump()),
        "declared_status": package.declared_status, "approval_claim_present": package.approval_record_ref is not None,
        "snapshot_origin": snapshot.origin, "snapshot_without_expiry": snapshot.valid_until is None,
        "package_selector_conflicts": conflict_count,
        "fields": [{"fact": row["fact"], "status": row["status"], "matched_rule_count": len(row["matched_rules"])}
                   for row in proposals],
        "binding_verified": True, **flags,
    }
    artifact = {"schema_version": "customer-mapping-preview-v1-draft", "material_role": "mapping_proposal",
                "inputs": {"mapping": package.model_dump(), "snapshot": snapshot.model_dump(),
                           "context": context.model_dump()}, "proposals": proposals, "report": report}
    return MappingPreview(copy.deepcopy(artifact), copy.deepcopy(report))


def verify_preview(raw: dict) -> dict:
    """Replay full artifact; cannot authenticate the original values or approvals."""
    try:
        inputs = raw["inputs"]
        expected = preview_mapping(inputs["mapping"], inputs["snapshot"], context=inputs["context"])
        require(value_digest(raw) == value_digest(expected.artifact), "mapping_preview_replay_mismatch")
        return {**expected.report, "replay_verified": True}
    except (KeyError, TypeError):
        raise FactContractError("invalid_mapping_preview") from None
