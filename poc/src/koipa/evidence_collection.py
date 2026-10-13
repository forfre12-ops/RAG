"""Offline snapshot adapter for policy-facts-v1; no extraction, network or authority.

Only explicitly supplied canonical management observations become assertions.
Text is evidence material, never a source of invented management/public facts.
Incomplete extraction is retained as diagnostics but produces no usable packet.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import Field, ValidationError

from koipa.modules.m3_labeling.policy_engine import Policy
from koipa.policy_facts import (
    FACT_TYPES, MANAGEMENT_FACTS, ContractModel, Digest, FactContext, FactContractError,
    FactEstimate, FactPacket, NonEmpty, require, resolve_packet, text_digest, value_digest,
)
from koipa.policy_shadow import evaluate_shadow, policy_digest

ManagementFact = Literal[
    "security_marking", "access_scope", "owner_org", "dlp_label", "actual_reader_scope",
]
MARKINGS = frozenset({"top_secret", "secret", "confidential", "none"})
ACCESS_SCOPES = frozenset({"approved_only", "designated", "department", "all_employees"})


class DocumentBinding(ContractModel):
    org_id: NonEmpty
    document_id: NonEmpty
    document_revision: NonEmpty
    original_sha256: Digest
    document_sha256: Digest


class CollectionContext(DocumentBinding):
    """Independent caller expectation; declarations, not authenticated identity."""

    as_of: NonEmpty

    def fact_context(self) -> FactContext:
        return FactContext(**{key: getattr(self, key) for key in FactContext.model_fields})


class ExtractionSnapshot(DocumentBinding):
    extraction_id: NonEmpty
    extractor_version: NonEmpty
    method: NonEmpty
    text: str
    captured_at: NonEmpty
    source_ref: NonEmpty
    completeness: Literal["complete", "incomplete", "unknown"] = "unknown"
    table_coverage: Literal["complete", "incomplete", "unknown", "not_applicable"] = "unknown"
    pages: Annotated[int, Field(ge=0)] | None = None
    total_pages: Annotated[int, Field(ge=0)] | None = None
    ocr_used: bool = False
    quality: Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)] | None = None
    warnings: list[NonEmpty] = Field(default_factory=list)
    error: NonEmpty | None = None


class ManagementObservation(ContractModel):
    state: Literal["observed", "proven_absent", "unknown"]
    # Required even for unknown/absence: missing is not explicit null.
    value: NonEmpty | None


class ManagementSnapshot(DocumentBinding):
    snapshot_id: NonEmpty
    provider_ref: NonEmpty
    captured_at: NonEmpty
    valid_until: NonEmpty | None = None
    fields: dict[ManagementFact, ManagementObservation]


class CollectionInput(ContractModel):
    schema_version: Literal["policy-evidence-collection-v1-draft"]
    material_role: Literal["unverified_supplied_assertions", "synthetic_policy_fixture"] = "unverified_supplied_assertions"
    extraction: ExtractionSnapshot
    management: list[ManagementSnapshot] = Field(default_factory=list)
    estimates: list[FactEstimate] = Field(default_factory=list)


@dataclass(frozen=True)
class CollectionResult:
    # Never export a packet on an extraction hold; older packet consumers do not
    # understand collection completeness and could otherwise publish a candidate.
    packet: FactPacket | None
    report: dict[str, Any]


def _binding(snapshot: DocumentBinding, context: CollectionContext) -> None:
    require(all(getattr(snapshot, key) == getattr(context, key)
                for key in DocumentBinding.model_fields), "collection_document_binding_mismatch")


def _coverage(snapshot: ExtractionSnapshot) -> list[str]:
    reasons = []
    if not any(not c.isspace() and c not in "\ufffc\ufeff\u200b" for c in snapshot.text):
        reasons.append("extracted_text_empty")
    if snapshot.completeness != "complete":
        reasons.append("extraction_completeness_" + snapshot.completeness)
    if snapshot.table_coverage in {"unknown", "incomplete"}:
        reasons.append("table_coverage_" + snapshot.table_coverage)
    if snapshot.error is not None:
        reasons.append("extraction_error_reported")
    if snapshot.warnings:
        reasons.append("extraction_warnings_reported")
    pages, total = snapshot.pages, snapshot.total_pages
    if pages is not None and total is not None:
        require(pages <= total, "extraction_page_counts_invalid")
        if pages == 0 or pages < total:
            reasons.append("extraction_pages_incomplete")
    elif pages is not None or total is not None:
        reasons.append("extraction_page_coverage_unknown")
    return reasons


def collect_evidence(raw: dict, *, context: CollectionContext, policy: Policy) -> CollectionResult:
    """Copy, bind, validate and translate supplied snapshots; never modify inputs.

    Raw customer aliases are NOT translated. Exporters must supply explicit
    canonical observations, with any upstream normalization separately reviewed.
    """
    try:
        supplied = CollectionInput.model_validate(copy.deepcopy(raw))
        context = CollectionContext.model_validate(context.model_dump(warnings=False))
    except (ValidationError, AttributeError, TypeError):
        raise FactContractError("invalid_collection_contract") from None
    extraction = supplied.extraction
    _binding(extraction, context)
    require(text_digest(extraction.text) == context.document_sha256, "collection_text_hash_mismatch")
    hold_reasons = _coverage(extraction)
    fact_context = context.fact_context()
    scope = {key: getattr(context, key) for key in ("org_id", "document_id", "document_sha256")}
    extraction_record = extraction.model_dump(exclude={"text"})
    record_sha = value_digest(extraction_record)
    text_id = "extraction-text-" + record_sha
    sources = [
        {**scope, "source_id": text_id, "kind": "document_text", "payload": extraction.text,
         "payload_sha256": context.document_sha256, "captured_at": extraction.captured_at,
         "source_ref": extraction.source_ref},
        {**scope, "source_id": "extraction-record-" + record_sha, "kind": "system_metadata",
         "payload": extraction_record, "payload_sha256": record_sha,
         "captured_at": extraction.captured_at, "source_ref": extraction.source_ref},
    ]
    claims = []
    snapshot_ids = set()
    for snapshot in supplied.management:
        _binding(snapshot, context)
        snapshot_key = (snapshot.provider_ref, snapshot.snapshot_id)
        require(snapshot_key not in snapshot_ids, "duplicate_management_snapshot")
        snapshot_ids.add(snapshot_key)
        # Include revision, times and provider declaration in the payload binding.
        # No normalization is hidden between source values and asserted values.
        payload = snapshot.model_dump()
        sha = value_digest(payload)
        sid = "management-" + sha
        sources.append({**scope, "source_id": sid, "kind": "system_metadata", "payload": payload,
                        "payload_sha256": sha, "captured_at": snapshot.captured_at,
                        "valid_until": snapshot.valid_until, "source_ref": snapshot.provider_ref})
        for fact, observation in sorted(snapshot.fields.items()):
            if observation.state == "unknown":
                require(observation.value is None, "unknown_management_has_value")
                continue
            if observation.state == "observed":
                require(observation.value is not None, "observed_management_requires_value")
                vocabulary = MARKINGS if fact == "security_marking" else ACCESS_SCOPES if fact == "access_scope" else None
                require(vocabulary is None or observation.value in vocabulary, "noncanonical_management_value")
            else:
                require(observation.value is None, "absent_management_requires_null")
            claims.append({"fact": fact, "state": observation.state, "value": observation.value,
                           "origin": "system_metadata", "evidence": [{
                               "source_id": sid, "source_sha256": sha,
                               "locator": {"kind": "json_pointer", "pointer": f"/fields/{fact}/value"},
                               "value_sha256": value_digest(observation.value),
                           }]})
    raw_packet = {**scope, "schema_version": "policy-facts-v1-draft", "material_role": supplied.material_role,
                  "policy_version": policy.version, "policy_sha256": policy_digest(policy),
                  "sources": sources, "facts": claims,
                  "estimates": [estimate.model_dump() for estimate in supplied.estimates]}
    packet, resolved = resolve_packet(raw_packet, fact_context, policy_version=policy.version,
                                      policy_sha256=policy_digest(policy))
    # Validate the policy even on extraction holds, but never publish its candidate
    # from an incomplete document. No serving routing is modified by this call.
    proposal = evaluate_shadow(policy, packet.model_dump(), context=fact_context)
    report = {
        "schema_version": "policy-evidence-collection-report-v1-draft",
        "status": "held_extraction" if hold_reasons else "ready_for_shadow",
        "material_role": supplied.material_role,
        "hold_reasons": hold_reasons,
        "collection_input_sha256": value_digest(supplied.model_dump()),
        "context_sha256": value_digest(context.model_dump()),
        "packet_sha256": None if hold_reasons else value_digest(packet.model_dump()),
        "lineage": {"original_binary_sha256": context.original_sha256,
                    "extracted_text_sha256": context.document_sha256,
                    "extraction_record_sha256": record_sha,
                    "policy_sha256": policy_digest(policy)},
        "management_snapshots": len(supplied.management),
        "management_without_expiry": sum(s.valid_until is None for s in supplied.management),
        "fact_states": {name: resolved[name].state for name in FACT_TYPES},
        "missing_management": sorted(name for name in MANAGEMENT_FACTS if resolved[name].state == "unknown"),
        "conflicting_facts": sorted(name for name in FACT_TYPES if resolved[name].state == "conflict"),
        "estimate_count": len(supplied.estimates), "estimates_used_for_policy": False,
        "policy_proposal": None if hold_reasons else proposal,
        "packet_available": not hold_reasons,
        "collection_binding_verified": True,
        "original_binary_bytes_verified": False,
        "evidence_authenticity_verified": False,
        "extraction_completeness_independently_verified": False,
        "freshness_policy_verified": False,
        "policy_approval_verified": False,
        "semantic_truth_verified": False,
        "training_allowed": False, "model_evaluation_allowed": False,
        "customer_accuracy_measured": False, "automation_allowed": False, "finalized": False,
    }
    return CollectionResult(None if hold_reasons else packet, report)
