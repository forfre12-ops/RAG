"""Draft, offline evidence-binding contract; validates bindings, not truth or authority."""
from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

FACT_TYPES = {
    "public_disclosed": "boolean", "has_concrete_parameters": "boolean",
    "content_kinds": "string_list", "legal_protection_basis": "string",
    "security_marking": "string", "access_scope": "string", "owner_org": "string",
    "dlp_label": "string", "actual_reader_scope": "string",
}
MANAGEMENT_FACTS = {"security_marking", "access_scope", "owner_org", "dlp_label", "actual_reader_scope"}
FACTOR_EVIDENCE_GROUPS = {
    "secrecy": ("public_disclosed",),
    "value": ("content_kinds", "has_concrete_parameters"),
    "management": tuple(sorted(MANAGEMENT_FACTS)),
    "other": ("legal_protection_basis",),
}
NonEmpty = Annotated[str, Field(min_length=1, pattern=r"\S")]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Origin = Literal["document_text", "system_metadata", "public_source", "human_review"]


class FactContractError(ValueError):
    """Stable error code only; never include document/source payloads in errors."""


def require(condition: bool, code: str) -> None:
    if not condition:
        raise FactContractError(code)


def text_digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def value_digest(value: Any) -> str:
    try:
        return text_digest(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                      separators=(",", ":"), allow_nan=False))
    except (TypeError, ValueError, OverflowError):
        raise FactContractError("non_json_value") from None


def timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        require(result.utcoffset() is not None, "timestamp_timezone_required")
        return result
    except (TypeError, ValueError):
        raise FactContractError("invalid_timestamp") from None


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, revalidate_instances="always")


class FactContext(ContractModel):
    """Caller supplies scope independently; this object is not authentication."""

    org_id: NonEmpty
    document_id: NonEmpty
    document_sha256: Digest
    as_of: NonEmpty


class TextSpan(ContractModel):
    kind: Literal["text_span"]
    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(gt=0)]


class JsonPointer(ContractModel):
    kind: Literal["json_pointer"]
    pointer: NonEmpty


class EvidenceRef(ContractModel):
    source_id: NonEmpty
    source_sha256: Digest
    locator: Annotated[TextSpan | JsonPointer, Field(discriminator="kind")]
    value_sha256: Digest


class EvidenceSource(ContractModel):
    source_id: NonEmpty
    kind: Origin
    org_id: NonEmpty
    document_id: NonEmpty
    document_sha256: Digest
    payload: str | dict[str, Any]
    payload_sha256: Digest
    captured_at: NonEmpty
    valid_until: NonEmpty | None = None
    source_ref: NonEmpty


class FactAssertion(ContractModel):
    fact: NonEmpty
    state: Literal["observed", "proven_absent", "unknown"]
    value: Any = None
    origin: Origin | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)


class FactEstimate(ContractModel):
    fact: NonEmpty
    value: Any
    origin: Literal["model_estimated", "rule_estimated"]
    producer_ref: NonEmpty


class FactPacket(ContractModel):
    schema_version: Literal["policy-facts-v1-draft"]
    material_role: Literal["unverified_supplied_assertions", "synthetic_policy_fixture"] = "unverified_supplied_assertions"
    org_id: NonEmpty
    document_id: NonEmpty
    document_sha256: Digest
    policy_version: NonEmpty
    policy_sha256: Digest
    sources: list[EvidenceSource] = Field(default_factory=list)
    facts: list[FactAssertion] = Field(default_factory=list)
    estimates: list[FactEstimate] = Field(default_factory=list)


@dataclass(frozen=True)
class ResolvedFact:
    state: str
    value: Any = None
    source_ids: tuple[str, ...] = ()


def valid_value(fact: str, value: Any) -> bool:
    kind = FACT_TYPES.get(fact)
    if kind == "boolean":
        return type(value) is bool
    if kind == "string":
        return value is None or isinstance(value, str) and bool(value.strip())
    if kind == "string_list":
        return (isinstance(value, list) and all(isinstance(x, str) and x.strip() for x in value)
                and len(value) == len(set(value)))
    return False


def _absent(fact: str, value: Any) -> bool:
    kind = FACT_TYPES[fact]
    return (value is False if kind == "boolean" else value == [] if kind == "string_list"
            else value is None)


def _fact_value(fact: str, value: Any) -> Any:
    # Content kinds are an unordered vocabulary set; source/locator hashes remain byte/order exact.
    return sorted(value) if FACT_TYPES[fact] == "string_list" else value


def _pointer(payload: dict, pointer: str) -> Any:
    require(pointer.startswith("/"), "invalid_json_pointer")
    current: Any = payload
    for token in pointer[1:].split("/"):
        require(re.search(r"~(?![01])", token) is None, "invalid_json_pointer_escape")
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            require(key in current, "evidence_pointer_missing")
            current = current[key]
        elif isinstance(current, list):
            require(bool(re.fullmatch(r"0|[1-9][0-9]*", key)), "invalid_array_pointer")
            require(int(key) < len(current), "evidence_pointer_missing")
            current = current[int(key)]
        else:
            raise FactContractError("evidence_pointer_missing")
    return current


def _check_source(source: EvidenceSource, context: FactContext) -> None:
    require((source.org_id, source.document_id, source.document_sha256)
            == (context.org_id, context.document_id, context.document_sha256), "source_scope_mismatch")
    captured, as_of = timestamp(source.captured_at), timestamp(context.as_of)
    require(captured <= as_of, "source_from_future")
    if source.valid_until is not None:
        valid_until = timestamp(source.valid_until)
        require(captured <= valid_until and as_of < valid_until, "source_expired_or_invalid_window")
    if source.kind in {"document_text", "public_source"}:
        require(isinstance(source.payload, str), "source_payload_type_mismatch")
        digest = text_digest(source.payload)
    else:
        require(isinstance(source.payload, dict), "source_payload_type_mismatch")
        digest = value_digest(source.payload)
    require(digest == source.payload_sha256, "source_payload_hash_mismatch")
    if source.kind == "document_text":
        require(digest == context.document_sha256, "document_body_hash_mismatch")
    if source.kind == "public_source":
        require(source.source_ref.startswith(("https://", "http://")), "public_source_reference_required")


def _check_assertion(assertion: FactAssertion, sources: dict[str, EvidenceSource]) -> None:
    require(assertion.fact in FACT_TYPES, "unknown_fact_name")
    if assertion.state == "unknown":
        require(assertion.value is None and assertion.origin is None and not assertion.evidence,
                "unknown_must_not_carry_asserted_value")
        return
    require(valid_value(assertion.fact, assertion.value), "invalid_fact_value")
    require((assertion.state == "proven_absent") == _absent(assertion.fact, assertion.value),
            "fact_state_value_mismatch")
    require(assertion.origin is not None and bool(assertion.evidence), "known_fact_requires_evidence")
    if assertion.fact in MANAGEMENT_FACTS:
        require(assertion.origin in {"system_metadata", "human_review"}, "management_requires_supplied_evidence")
    if assertion.fact == "public_disclosed":
        require(assertion.origin in {"public_source", "human_review"}, "public_status_requires_external_evidence")
    if assertion.state == "proven_absent":
        require(assertion.origin in {"system_metadata", "human_review"}, "absence_requires_explicit_record")
    for ref in assertion.evidence:
        require(ref.source_id in sources, "evidence_source_missing")
        source = sources[ref.source_id]
        require(source.kind == assertion.origin, "evidence_origin_mismatch")
        require(source.payload_sha256 == ref.source_sha256, "evidence_source_hash_mismatch")
        if isinstance(ref.locator, TextSpan):
            require(isinstance(source.payload, str), "locator_payload_type_mismatch")
            start, end = ref.locator.start, ref.locator.end
            require(0 <= start < end <= len(source.payload), "invalid_text_span")
            digest = text_digest(source.payload[start:end])
        else:
            require(isinstance(source.payload, dict), "locator_payload_type_mismatch")
            value = _pointer(source.payload, ref.locator.pointer)
            # Strict canonical equality: False cannot silently equal numeric zero.
            require(value_digest(value) == value_digest(assertion.value), "evidence_value_mismatch")
            digest = value_digest(value)
        require(digest == ref.value_sha256, "evidence_location_hash_mismatch")


def resolve_packet(raw: dict, context: FactContext, *, policy_version: str, policy_sha256: str):
    """Return a private snapshot and resolved assertions; no I/O, models or authentication."""
    try:
        packet = FactPacket.model_validate(copy.deepcopy(raw))
        # Revalidate instances too; model_construct is not a bypass at this boundary.
        context = FactContext.model_validate(context.model_dump())
    except (ValidationError, AttributeError, TypeError):
        raise FactContractError("invalid_fact_contract") from None
    require((packet.org_id, packet.document_id, packet.document_sha256)
            == (context.org_id, context.document_id, context.document_sha256), "packet_scope_mismatch")
    require((packet.policy_version, packet.policy_sha256) == (policy_version, policy_sha256),
            "packet_policy_mismatch")
    timestamp(context.as_of)
    sources = {s.source_id: s for s in packet.sources}
    require(len(sources) == len(packet.sources), "duplicate_source_id")
    for source in sources.values():
        _check_source(source, context)
    claims: dict[str, list[FactAssertion]] = {name: [] for name in FACT_TYPES}
    for assertion in packet.facts:
        _check_assertion(assertion, sources)
        if assertion.state != "unknown":
            claims[assertion.fact].append(assertion)
    for estimate in packet.estimates:
        require(valid_value(estimate.fact, estimate.value), "invalid_estimate")
    resolved = {}
    for name, assertions in claims.items():
        values = {(a.state, value_digest(_fact_value(name, a.value))) for a in assertions}
        ids = tuple(sorted({e.source_id for a in assertions for e in a.evidence}))
        if not assertions:
            resolved[name] = ResolvedFact("unknown")
        elif len(values) > 1:
            resolved[name] = ResolvedFact("conflict", source_ids=ids)
        else:
            first = assertions[0]
            resolved[name] = ResolvedFact(first.state, copy.deepcopy(_fact_value(name, first.value)), ids)
    return packet, resolved
