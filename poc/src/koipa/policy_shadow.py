"""Evidence-aware policy proposals only. Never wired to production classification here."""
from __future__ import annotations

import copy
from dataclasses import asdict
from datetime import date, timezone

from pydantic import ValidationError

from koipa.modules.m3_labeling.policy_engine import Policy, Rule, _test, validate
from koipa.policy_facts import (
    FACT_TYPES, FACTOR_EVIDENCE_GROUPS, FactContext, FactContractError,
    ResolvedFact, require, resolve_packet, timestamp, valid_value, value_digest,
)


def policy_digest(policy: Policy) -> str:
    """Canonical parsed policy hash, not the raw policy file's byte hash."""
    return value_digest(asdict(policy))


def parse_shadow_policy(raw: dict) -> Policy:
    """Strict draft adapter: no int/string coercion or silently discarded rule fields."""
    require(isinstance(raw, dict), "invalid_policy_object")
    allowed = {"org_id", "policy_version", "effective_date", "grade_order", "rules", "default_grade", "note"}
    require(all(k in allowed or k.startswith("_") for k in raw), "unknown_policy_field")
    for field in ("org_id", "policy_version", "effective_date"):
        require(isinstance(raw.get(field), str) and bool(raw[field].strip()), "invalid_policy_identity")
    order, rows = raw.get("grade_order"), raw.get("rules")
    require(isinstance(order, list) and all(isinstance(x, str) and x.strip() for x in order), "invalid_grade_order")
    require(isinstance(rows, list), "invalid_policy_rules")
    rules = []
    for row in rows:
        require(isinstance(row, dict), "invalid_policy_rule")
        require(not set(row) - {"id", "grade", "priority", "when", "requires_evidence", "note"}, "unknown_rule_field")
        for field in ("id", "grade"):
            require(isinstance(row.get(field), str) and bool(row[field].strip()), "invalid_rule_identity")
        priority = row.get("priority", 100)
        evidence = row.get("requires_evidence", [])
        require(type(priority) is int and isinstance(row.get("when"), dict), "invalid_rule_shape")
        require(isinstance(evidence, list) and all(isinstance(x, str) for x in evidence), "invalid_evidence_names")
        require(isinstance(row.get("note", ""), str), "invalid_rule_note")
        rules.append(Rule(row["id"], row["grade"], priority, copy.deepcopy(row["when"]),
                          tuple(evidence), row.get("note", "")))
    require(isinstance(raw.get("default_grade", ""), str) and isinstance(raw.get("note", ""), str),
            "invalid_policy_defaults")
    return Policy(raw["org_id"], raw["policy_version"], raw["effective_date"], tuple(order),
                  tuple(rules), raw.get("default_grade", ""), raw.get("note", ""))


def _validate_policy(policy: Policy, context: FactContext) -> None:
    require(not validate(policy), "invalid_policy")
    require(policy.org_id == context.org_id, "policy_org_mismatch")
    require(len(set(policy.grade_order)) == len(policy.grade_order), "duplicate_grade_order")
    try:
        effective = date.fromisoformat(policy.effective_date)
    except ValueError:
        raise FactContractError("invalid_policy_effective_date") from None
    # Draft date convention is explicitly UTC; tenant-specific effective-time policy is not implemented.
    require(effective <= timestamp(context.as_of).astimezone(timezone.utc).date(), "policy_not_effective")
    for rule in policy.rules:
        require(type(rule.priority) is int, "invalid_policy_priority")
        require(all(e in FACT_TYPES for e in rule.requires_evidence), "unknown_required_evidence")
        for fact, spec in rule.when.items():
            require(isinstance(spec, dict), "invalid_condition")
            require(not set(spec) - {"op", "value"}, "unknown_condition_field")
            op, expected = spec.get("op", "eq"), spec.get("value")
            if op in {"exists", "missing"}:
                require(expected is None, "existence_operator_has_no_value")
            elif op in {"eq", "ne"}:
                require(valid_value(fact, expected), "condition_value_type_mismatch")
            elif op in {"in", "not_in"}:
                require(isinstance(expected, list) and bool(expected)
                        and all(valid_value(fact, item) for item in expected), "condition_value_type_mismatch")
            elif op == "contains_any":
                require(FACT_TYPES[fact] == "string_list" and valid_value(fact, expected)
                        and bool(expected), "condition_value_type_mismatch")
            else:
                # These nine facts have no numeric type; do not coerce bool/string to numbers.
                raise FactContractError("unsupported_typed_operator")


def _condition(fact: ResolvedFact, spec: dict) -> bool | None:
    if fact.state in {"unknown", "conflict"}:
        return None  # Missing input is never proof for a policy's 'missing' condition.
    op, expected, value = spec.get("op", "eq"), spec.get("value"), fact.value
    if isinstance(value, list):
        if op in {"eq", "ne"}:
            expected = sorted(expected)
        elif op in {"in", "not_in"}:
            expected = [sorted(item) for item in expected]
    if value is None and op in {"eq", "ne", "in", "not_in"}:
        if op in {"eq", "ne"}:
            equal = expected is None
            return equal if op == "eq" else not equal
        member = value in expected
        return member if op == "in" else not member
    return _test(op, value, expected)


def evaluate_shadow(policy: Policy, raw_packet: dict, *, context: FactContext) -> dict:
    """Compute a draft shadow result; source bindings do not prove source truth."""
    policy = copy.deepcopy(policy)
    try:
        context = FactContext.model_validate(context.model_dump())
        _validate_policy(policy, context)
    except FactContractError:
        raise
    except (ValidationError, AttributeError, TypeError, ValueError, KeyError):
        raise FactContractError("invalid_shadow_policy_or_context") from None
    digest = policy_digest(policy)
    packet, facts = resolve_packet(raw_packet, context, policy_version=policy.version, policy_sha256=digest)
    traces, matched, blocked = [], [], []
    for rule in sorted(policy.rules, key=lambda r: (r.priority, r.id)):
        conditions = {name: _condition(facts[name], spec) for name, spec in sorted(rule.when.items())}
        missing = sorted({name for name, result in conditions.items() if result is None}
                         | {name for name in rule.requires_evidence if facts[name].state in {"unknown", "conflict"}})
        if False in conditions.values():
            status, missing = "not_applicable", []
        elif missing:
            status = "blocked"
            blocked.append(rule)
        else:
            status = "matched"
            matched.append(rule)
        traces.append({"rule_id": rule.id, "grade": rule.grade, "priority": rule.priority,
                       "status": status, "conditions": conditions, "missing_evidence": missing})

    winner = matched[0] if matched else None
    top = [r for r in matched if winner and r.priority == winner.priority]
    conflicts = sorted(name for name, fact in facts.items() if fact.state == "conflict")
    conflicting_rules = [r.id for r in top] if len({r.grade for r in top}) > 1 else []
    relevant_blocked = [r.id for r in blocked if winner is None or r.priority <= winner.priority
                        or policy.severity(r.grade) > policy.severity(winner.grade)]
    if conflicts:
        status = "needs_evidence_conflict_review"
    elif conflicting_rules:
        status = "needs_policy_review"
    elif relevant_blocked:
        status = "needs_evidence"
    elif winner is None:
        status = "no_matching_rule"
    else:
        status = "candidate"
    grade = winner.grade if winner is not None and status == "candidate" else None
    return {
        "schema_version": "policy-shadow-v1-draft", "mode": "shadow_only", "status": status,
        "material_role": packet.material_role,
        "org_id": context.org_id, "document_id": context.document_id,
        "document_sha256": context.document_sha256, "as_of": context.as_of,
        "policy_version": policy.version, "policy_sha256": digest, "effective_date": policy.effective_date,
        "input_sha256": value_digest(packet.model_dump()), "grade": grade,
        "rule_id": winner.id if grade is not None else None,
        "matched_candidate": {"grade": winner.grade, "rule_id": winner.id} if winner else None,
        "would_require_review": status != "candidate", "rule_trace": traces,
        "blocked_rules": [r.id for r in blocked], "decision_blocked_rules": relevant_blocked,
        "missing_evidence": sorted({name for t in traces if t["rule_id"] in relevant_blocked
                                    for name in t["missing_evidence"]}),
        "conflicting_rules": conflicting_rules, "conflicting_facts": conflicts,
        "facts": {name: {"state": fact.state, "source_ids": list(fact.source_ids)} for name, fact in facts.items()},
        "factor_evidence_groups": {
            axis: {"bound_fact_names": [name for name in names if facts[name].state in {"observed", "proven_absent"}],
                   "unresolved_fact_names": [name for name in names if facts[name].state in {"unknown", "conflict"}]}
            for axis, names in FACTOR_EVIDENCE_GROUPS.items()
        },
        "estimates_used_for_policy": False, "estimate_count": len(packet.estimates),
        "evidence_binding_verified": True, "evidence_authenticity_verified": False,
        "semantic_truth_verified": False, "policy_approval_verified": False,
        "automation_allowed": False, "customer_accuracy_measured": False,
        "training_allowed": False, "model_evaluation_allowed": False, "finalized": False,
    }


def attach_shadow(existing_result: dict, *, policy: Policy | None = None,
                  packet: dict | None = None, context: FactContext | None = None) -> dict:
    """Pure, opt-in composition helper; not called by the serving/API path."""
    result = copy.deepcopy(existing_result)
    if policy is None:
        return result  # No policy: do not inspect inputs or introduce new output fields.
    require("policy_proposal" not in result, "existing_policy_proposal_would_be_overwritten")
    try:
        require(context is not None and packet is not None, "missing_shadow_input")
        result["policy_proposal"] = evaluate_shadow(policy, packet, context=context)
    except FactContractError as exc:
        result["policy_proposal"] = {
            "schema_version": "policy-shadow-v1-draft", "mode": "shadow_only",
            "status": "invalid_input", "grade": None, "would_require_review": True,
            "error_code": str(exc), "automation_allowed": False,
            "policy_approval_verified": False, "evidence_authenticity_verified": False,
            "semantic_truth_verified": False, "evidence_binding_verified": False,
            "customer_accuracy_measured": False,
            "training_allowed": False, "model_evaluation_allowed": False, "finalized": False,
        }
    return result
