"""Offline internal policy for registered short fictional bodies, not serving.

Text facts and synthetic context are separate. No expected grade is accepted.
Human signatures, customer policy authority and ML eligibility are never issued.
"""
from __future__ import annotations

import copy
import sqlite3
from typing import Literal

from pydantic import ValidationError

from koipa.policy_facts import ContractModel, FactContractError, require, text_digest, value_digest
from koipa.short_body_facts import FLAGS, extract_facts, semantic_checks
from koipa.short_body_reference import REFERENCE, anchored_answer, arithmetic_oracle

POLICY = {"policy_id": "short-body-content-reference", "version": "0.3", "scope": "registered_fictional_bodies_only",
          "rules": {"R-CORE": "consistent complete program + current core + private -> TS",
                    "R-DETAIL": "program/observations/negotiation + private, not core-complete -> S1",
                    "R-OPS": "concrete allocation/receipt/transfer + private -> S2",
                    "R-BLANK": "all supplied fields placeholders, no conflict -> S3",
                    "R-RELEASED": "this revision authorized released, not private, other high risk absent -> S3",
                    "R-HOLD": "unsupported/unregistered/inconsistent body, incomplete scope, unknown relevant facts or conflicting visibility"},
          "authority": "internal_conditional_reference_only", "customer_policy_approved": False}
POLICY_SHA256 = value_digest(POLICY)
REFERENCE_SHA256 = value_digest(REFERENCE)


class SyntheticContext(ContractModel):
    origin: Literal["synthetic_assumption"]
    scope_complete: bool | None
    private_current_revision: bool | None
    release_authorized_for_this_revision: bool | None
    current_core_asset: bool | None
    other_high_risk_material: bool | None
    world: Literal["fictional; no real persons, access secrets or customer assets"]


def _parse_context(raw):
    try:
        if isinstance(raw, SyntheticContext):
            raw = raw.model_dump()
        return SyntheticContext.model_validate(copy.deepcopy(raw))
    except (TypeError, ValueError, ValidationError):
        raise FactContractError("short_reference_context_invalid") from None


def certify_body(text):
    require(value_digest(REFERENCE) == REFERENCE_SHA256, "short_reference_answer_runtime_drift")
    extracted = extract_facts(text)
    if extracted["status"] != "supported_bounded_form" or text_digest(text) not in REFERENCE:
        return {**FLAGS, "status": "not_fixed", "document_sha256": text_digest(text),
                "reason": "unsupported_or_unregistered_body", "extracted": extracted}
    reference = anchored_answer(text)
    require(value_digest(extracted["facts"]) == value_digest(reference), "short_reference_fact_disagreement")
    semantics = semantic_checks(extracted)
    oracle = arithmetic_oracle(reference)
    if oracle is not None:
        require(value_digest(oracle) == value_digest(semantics["checks"]["finite_function"]["outputs"]), "short_reference_arithmetic_disagreement")
    return {**FLAGS, "schema_version": "short-body-fact-certificate-v1", "status": "fixed_text_facts",
            "document_sha256": text_digest(text), "reference_sha256": REFERENCE_SHA256,
            "facts": extracted["facts"], "facts_sha256": extracted["facts_sha256"],
            "semantics": semantics, "literal_anchor_agrees": True, "arithmetic_oracle_agrees": oracle is not None,
            "scope": "entire_supplied_text_only", "general_natural_language_supported": False}


def verify_body_certificate(text, certificate):
    require(value_digest(certify_body(text)) == value_digest(certificate), "short_reference_certificate_mismatch")
    return True


def features(certificate):
    if certificate["status"] != "fixed_text_facts":
        return {"registered": False, "consistent": False, "complete": False, "detail": False, "ops": False, "blank": False}
    values = {f["name"]: f["value"] for f in certificate["facts"]}
    return {"registered": True, "consistent": not certificate["semantics"]["warnings"],
            "complete": bool(values.get("program") and "input_domain" in values and "test_vectors" in values),
            "detail": bool(values.get("program") or "observations" in values or "quoted_unit_price" in values),
            "ops": bool(set(values) & {"work_slot", "receipt", "assignments", "transfer"}),
            "blank": bool(values.get("form_fields")) and all(f["kind"] == "placeholder" for f in values["form_fields"])}


def decide(f, c):
    """Direct branch implementation. Unknown private/release is never complemented."""
    if not f["registered"] or not f["consistent"]:
        return None, "R-HOLD", "unregistered_or_inconsistent_body"
    if c.scope_complete is not True or c.other_high_risk_material is not False:
        return None, "R-HOLD", "scope_or_other_risk_unresolved"
    p, r = c.private_current_revision, c.release_authorized_for_this_revision
    if p is not None and r is not None and p == r:
        return None, "R-HOLD", "visibility_conflict_or_unresolved"
    if f["blank"]:
        return "S3", "R-BLANK", None
    if r is True and p is False:
        return "S3", "R-RELEASED", None
    if p is not True or r is not False:
        return None, "R-HOLD", "visibility_evidence_required"
    if f["complete"]:
        if c.current_core_asset is None:
            return None, "R-HOLD", "core_asset_evidence_required"
        if c.current_core_asset:
            return "TS", "R-CORE", None
    if f["detail"]:
        return "S1", "R-DETAIL", None
    if f["ops"]:
        return "S2", "R-OPS", None
    return None, "R-HOLD", "policy_gap"


def sql_decision(f, c):
    """Separate CASE decision path. SQLite is in memory, no production DB."""
    params = {**f, **c.model_dump()}
    with sqlite3.connect(":memory:") as conn:
        return conn.execute("""SELECT CASE
          WHEN :registered IS NOT 1 OR :consistent IS NOT 1 THEN NULL
          WHEN :scope_complete IS NOT 1 OR :other_high_risk_material IS NOT 0 THEN NULL
          WHEN :private_current_revision = :release_authorized_for_this_revision THEN NULL
          WHEN :blank IS 1 THEN 'S3'
          WHEN :private_current_revision IS 0 AND :release_authorized_for_this_revision IS 1 THEN 'S3'
          WHEN :private_current_revision IS NOT 1 OR :release_authorized_for_this_revision IS NOT 0 THEN NULL
          WHEN :complete IS 1 AND :current_core_asset IS NULL THEN NULL
          WHEN :complete IS 1 AND :current_core_asset IS 1 THEN 'TS'
          WHEN :detail IS 1 THEN 'S1'
          WHEN :ops IS 1 THEN 'S2'
          ELSE NULL END""", params).fetchone()[0]


def apply_reference_policy(text, context, *, policy_sha256=POLICY_SHA256):
    require(value_digest(POLICY) == POLICY_SHA256 == policy_sha256, "short_reference_policy_mismatch")
    c = _parse_context(context)
    cert = certify_body(text)
    derived = features(cert)
    grade, rule, reason = decide(derived, c)
    require(sql_decision(derived, c) == grade, "short_reference_policy_oracle_disagreement")
    exclusion_codes = {
        "TS": {"S1": "core_complete_higher_priority", "S2": "core_complete_higher_priority", "S3": "private_not_blank_or_released"},
        "S1": {"TS": "not_core_complete", "S2": "concrete_detail_higher_priority", "S3": "private_not_blank_or_released"},
        "S2": {"TS": "no_core_complete_basis", "S1": "no_concrete_detail_basis", "S3": "private_not_blank_or_released"},
        "S3": {"TS": "blank_or_authorized_release_with_scope_exclusions", "S1": "blank_or_authorized_release_with_scope_exclusions",
               "S2": "blank_or_authorized_release_with_scope_exclusions"},
    }
    return {**FLAGS, "schema_version": "short-body-policy-reference-v0.3", "policy_sha256": POLICY_SHA256,
            "document_sha256": text_digest(text), "context_sha256": value_digest(c.model_dump()),
            "body_certificate_sha256": value_digest(cert), "status": "fixed_under_internal_policy" if grade else "hold",
            "reference_grade": grade, "rule": rule, "reason": reason, "derived_from_certified_body": derived,
            "other_grade_exclusions": exclusion_codes[grade] if grade else {},
            "context_basis": [{"pointer": "/" + key, "value_sha256": value_digest(value), "origin": "synthetic_assumption"}
                              for key, value in c.model_dump().items()],
            "sql_policy_agrees": True, "customer_policy_approved": False, "real_context_verified": False}
