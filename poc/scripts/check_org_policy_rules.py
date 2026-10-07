#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Bounded, offline policy probes. Findings are not customer grade answers.

Selection witnesses prove only that a rule can be selected for that input.
No witness is NOT a proof of unreachability. Same-priority different grades
are potential conflicts; report an observed overlap separately.
Exit 0: sampled checks found no issue (NOT approval), 2: invalid input/output,
3: findings or incomplete checks. Existing reports are never overwritten.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import sys
from pathlib import Path

_POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_POC / "src"))

from koipa.modules.m3_labeling.policy_engine import (  # noqa: E402
    Policy, Rule, _condition_results, _test, evaluate, load, validate,
)

_SAMPLES: dict[str, tuple] = {
    "public_disclosed": (True, False),
    "has_concrete_parameters": (True, False),
    "content_kinds": (["가격", "원가", "입찰", "설계도", "소스코드"], ["일반 안내"]),
    "legal_protection_basis": ("가상 정책 근거", ""),
    "security_marking": ("top_secret", "none"),
    "access_scope": ("approved_only", "all_employees"),
    "owner_org": ("가상 부서", ""),
    "dlp_label": ("restricted", ""),
    "actual_reader_scope": ("department", "all_employees"),
}
_EMPTY = (None, "", [], {})


def _key(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _present(value):
    return value not in _EMPTY


def _domain(fact: str, specs: list[dict]) -> list:
    """Representative values only: not a complete logical domain."""
    candidates = [None, *_SAMPLES.get(fact, ("probe-a", "probe-b"))]
    tokens = []
    for spec in specs:
        op, want = str(spec.get("op", "eq")), spec.get("value")
        if op in ("in", "not_in"):
            candidates.extend(want or [])
        elif op == "contains_any":
            tokens.extend(want or [])
            candidates.extend([[item] for item in (want or [])])
            candidates.append([])
        elif op in ("gte", "lte"):
            boundary = float(want)
            if not math.isfinite(boundary):
                raise ValueError("Nonfinite policy comparison")
            candidates.extend((boundary, math.nextafter(boundary, -math.inf),
                               math.nextafter(boundary, math.inf)))
        elif op not in ("exists", "missing"):
            candidates.append(want)
    if tokens:
        candidates.append(list(dict.fromkeys(tokens)))
    # Avoid the ENTIRE exclusion list, not merely its first value.
    other = "__policy_probe_other__"
    while other in candidates:
        other += "_"
    numeric = any(spec.get("op") in ("gte", "lte") for spec in specs)
    if numeric:
        # Do not manufacture a nonnumeric input solely for numeric boundary probes.
        candidates = [v for v in candidates if v is None
                      or isinstance(v, (int, float)) and math.isfinite(v)]
    else:
        candidates.append(other)
    unique = {}
    for value in candidates:
        unique.setdefault(_key(value), value)
    return list(unique.values())


def _matches(spec: dict, value) -> bool:
    op = str(spec.get("op", "eq"))
    if not _present(value) and op not in ("exists", "missing"):
        return False
    try:
        return _test(op, value, spec.get("value"))
    except (TypeError, ValueError, OverflowError):
        return False


def _domains(policy: Policy) -> dict:
    specs: dict[str, list] = {}
    for rule in policy.rules:
        for fact, spec in rule.when.items():
            specs.setdefault(fact, []).append(spec)
        for fact in rule.requires_evidence:
            specs.setdefault(fact, [])
    return {fact: _domain(fact, specs[fact]) for fact in sorted(specs)}


def _facts_for(rule: Rule, domains: dict) -> dict:
    result = {}
    for fact in sorted(set(rule.when) | set(rule.requires_evidence)):
        candidates = domains[fact]
        if fact in rule.when:
            candidates = [v for v in candidates if _matches(rule.when[fact], v)]
        if fact in rule.requires_evidence:
            candidates = [v for v in candidates if _present(v)]
        if candidates and candidates[0] is not None:
            result[fact] = candidates[0]
    return result


def _applicable(rule: Rule, facts: dict) -> bool:
    matched, unknown = _condition_results(rule, facts)
    return (bool(matched) and not unknown
            and all(_present(facts.get(e)) for e in rule.requires_evidence))


def _probes(policy: Policy, domains: dict, limit: int):
    def generate():
        # Targeted rule probes first, then representative Cartesian combinations.
        for rule in sorted(policy.rules, key=lambda r: (r.priority, r.id)):
            yield _facts_for(rule, domains)
        for values in itertools.product(*domains.values()):
            yield {name: value for name, value in zip(domains, values) if value is not None}

    seen, result = set(), []
    for facts in generate():
        key = _key(facts)
        if key in seen:
            continue
        seen.add(key)
        if len(result) == limit:
            return result, True
        result.append(facts)
    return result, False


def check(policy: Policy, *, probe_limit: int = 4096) -> dict:
    if type(probe_limit) is not int or probe_limit < 1 or probe_limit > 100_000:
        raise ValueError("probe_limit must be an integer between 1 and 100000")
    issues = validate(policy)
    if issues:
        raise ValueError("; ".join(issues))
    domains = _domains(policy)
    probes, limited = _probes(policy, domains, probe_limit)
    evaluations = [(facts, evaluate(policy, facts)) for facts in probes]
    out = {
        "schema_version": "policy-probe-audit-v2", "rules": [], "priority_conflicts": [],
        "potential_priority_conflicts": [], "unreachable": [], "inert_conditions": [],
        "incomplete_checks": ["probe_limit_reached"] if limited else [],
        "exhaustive_proof": False, "policy_approval_granted": False,
        "customer_accuracy_measured": False, "claim_scope": "synthetic_policy_probes_only",
        "probe_coverage": {
            "evaluated": len(probes), "limit": probe_limit, "limit_reached": limited,
            "domain_sizes": {name: len(values) for name, values in domains.items()},
            "representative_domain_only": True,
            "count_scope": "base probes; targeted mutation/evidence checks are reported per rule",
        },
        "legacy_unreachable_field_meaning": "not selected in generated probes; not a proof",
    }

    # Different grades at equal priority need a simultaneous-match witness.
    ordered = sorted(policy.rules, key=lambda r: (r.priority, r.id))
    for left, right in itertools.combinations(ordered, 2):
        if left.priority != right.priority or left.grade == right.grade:
            continue
        pair = {"priority": left.priority, "rules": [left.id, right.id],
                "grades": sorted({left.grade, right.grade})}
        out["potential_priority_conflicts"].append(pair)
        witness = next((f for f in probes if _applicable(left, f) and _applicable(right, f)), None)
        if witness is not None:
            out["priority_conflicts"].append({**pair, "facts": witness,
                                              "status": "OBSERVED_OVERLAP"})

    for rule in policy.rules:
        selected = next(((facts, got) for facts, got in evaluations if got.rule_id == rule.id), None)
        applicable = next(((facts, got) for facts, got in evaluations if _applicable(rule, facts)), None)
        facts, got = selected or applicable or (_facts_for(rule, domains), None)
        got = got or evaluate(policy, facts)
        row = {"rule": rule.id, "grade": rule.grade, "facts": facts,
               "got_rule": got.rule_id, "got_grade": got.grade, "reachable": selected is not None,
               "witness_applicable": _applicable(rule, facts)}
        if selected is None:
            out["unreachable"].append({
                "rule": rule.id, "shadowed_by": got.rule_id, "grade": rule.grade,
                "status": "NOT_SELECTED_IN_PROBES", "proof": False,
            })

        inert = []
        if selected is not None:
            for fact, spec in rule.when.items():
                # A mutation must actually violate the condition before its effect is measured.
                bad_values = [v for v in domains[fact] if not _matches(spec, v)]
                if not bad_values:
                    out["incomplete_checks"].append(f"mutation_probe_unavailable:{rule.id}:{fact}")
                    continue
                for bad in bad_values:
                    probe = dict(facts)
                    if bad is None:
                        probe.pop(fact, None)
                    else:
                        probe[fact] = bad
                    if evaluate(policy, probe).rule_id == rule.id:
                        inert.append(fact)
                        break
        if inert:
            out["inert_conditions"].append({"rule": rule.id, "conditions": inert})
        row["inert_conditions"] = inert

        evidence_rows = []
        for ev in rule.requires_evidence:
            probe = {k: v for k, v in facts.items() if k != ev}
            res = evaluate(policy, probe)
            downgraded = (row["witness_applicable"] and res.grade is not None
                          and 0 <= policy.severity(res.grade) < policy.severity(rule.grade)
                          and not res.needs_review)
            evidence_rows.append({
                "evidence": ev, "grade": res.grade, "needs_review": res.needs_review,
                "silent_downgrade": downgraded, "baseline_applicable": row["witness_applicable"],
            })
        row["evidence"] = evidence_rows
        out["rules"].append(row)
    out["silent_downgrades"] = [
        {"rule": r["rule"], "evidence": e["evidence"]}
        for r in out["rules"] for e in r["evidence"] if e["silent_downgrade"]
    ]
    findings = any(out[k] for k in ("unreachable", "priority_conflicts", "inert_conditions", "silent_downgrades"))
    out["status"] = ("FINDINGS" if findings else "INCOMPLETE_CHECKS" if out["incomplete_checks"]
                     else "SAMPLED_NO_FINDINGS")
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("path", type=Path)
    ap.add_argument("--json", type=Path)
    ap.add_argument("--probe-limit", type=int, default=4096)
    args = ap.parse_args(argv)
    try:
        target = args.json
        if target is not None and not target.is_absolute():
            target = _POC / target
        if target is not None and target.exists():
            raise ValueError("Output already exists; choose a new report path")
        before = hashlib.sha256(args.path.read_bytes()).hexdigest()
        policy = load(args.path)
        report = check(policy, probe_limit=args.probe_limit)
        if hashlib.sha256(args.path.read_bytes()).hexdigest() != before:
            raise ValueError("Policy input changed during audit")
        report["policy_sha256"] = before
        report["source_sha256"] = {
            "check_org_policy_rules.py": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "policy_engine.py": hashlib.sha256(
                (_POC / "src/koipa/modules/m3_labeling/policy_engine.py").read_bytes()).hexdigest(),
        }
        if target is not None:
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("x", encoding="utf-8") as stream:
                json.dump(report, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
        print(json.dumps({"status": "INVALID_AUDIT", "error_type": type(exc).__name__,
                          "policy_approval_granted": False}, ensure_ascii=False))
        return 2

    print(json.dumps({
        "status": report["status"], "rules": len(report["rules"]),
        "probes": report["probe_coverage"]["evaluated"],
        "not_selected_in_probes": len(report["unreachable"]),
        "observed_conflicts": len(report["priority_conflicts"]),
        "incomplete_checks": report["incomplete_checks"],
        "exhaustive_proof": False, "policy_approval_granted": False,
    }, ensure_ascii=False, indent=2))
    return 3 if report["status"] != "SAMPLED_NO_FINDINGS" else 0


if __name__ == "__main__":
    raise SystemExit(main())
