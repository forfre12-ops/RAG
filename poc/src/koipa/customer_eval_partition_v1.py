"""Declared development exposure and fail-closed split proposals; no release grants.

This separate module preserves the frozen customer_benchmark implementation.
Hashes bind an inventory, not its author's identity or complete access history.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import re

from koipa.customer_benchmark import (
    FLAGS, GRADES, _groups, duplicate_audit, normalized, validate_answers, validate_documents,
)
from koipa.policy_facts import require, text_digest, value_digest

SCHEMA = "customer-development-exposure-v1"
FAMILIES = ("family_id", "scenario_id", "template_family_id")
HASHES = ("body_sha256", "normalized_body_sha256", "number_masked_body_sha256")
REASONS = {"diagnostic_fit_or_selection", "development_authoring", "model_selection"}


def _digest(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _identities(d):
    return {"body_sha256": text_digest(d.input.text),
            "normalized_body_sha256": text_digest(normalized(d.input.text)),
            "number_masked_body_sha256": text_digest(normalized(d.input.text, mask_numbers=True)),
            **{k: getattr(d, k) for k in FAMILIES}}


def _record(d, source_ref, source_sha256, reason):
    require(type(source_ref) is str and 0 < len(source_ref) <= 500 and bool(source_ref.strip()), "exposure_source_invalid")
    require(_digest(source_sha256) and reason in REASONS, "exposure_source_invalid")
    return {"doc_id": d.input.doc_id, "input_sha256": d.input_sha256, **_identities(d),
            "reason": reason, "source_ref": source_ref, "source_sha256": source_sha256}


def _seal(records):
    out = {**FLAGS, "schema_version": SCHEMA, "status": "declared_development_inventory_not_access_control",
           "records": sorted(records, key=lambda r: r["doc_id"]), "record_count": len(records),
           "complete_access_history_certified": False, "blindness_certified": False}
    out["ledger_sha256"] = value_digest(out)
    return out


def build_exposure_ledger(raw_documents, *, source_ref, source_sha256, reason):
    docs = validate_documents(raw_documents)
    return _seal([_record(d, source_ref, source_sha256, reason) for d in docs])


def validate_exposure_ledger(ledger, *, expected_ledger_sha256):
    require(type(ledger) is dict and _digest(expected_ledger_sha256), "exposure_ledger_required")
    wanted = set(FLAGS) | {"schema_version", "status", "records", "record_count", "complete_access_history_certified",
                           "blindness_certified", "ledger_sha256"}
    require(set(ledger) == wanted and ledger["schema_version"] == SCHEMA and
            ledger["status"] == "declared_development_inventory_not_access_control", "exposure_ledger_schema_invalid")
    require(all(type(ledger[k]) is bool and ledger[k] is False for k in (*FLAGS, "complete_access_history_certified", "blindness_certified")), "exposure_permission_invalid")
    require(ledger["ledger_sha256"] == expected_ledger_sha256 and
            value_digest({k: v for k, v in ledger.items() if k != "ledger_sha256"}) == expected_ledger_sha256, "exposure_ledger_hash_mismatch")
    records = ledger["records"]
    require(type(records) is list and 0 < len(records) <= 10000 and type(ledger["record_count"]) is int and
            ledger["record_count"] == len(records), "exposure_inventory_empty_or_invalid")
    fields = set(FAMILIES) | set(HASHES) | {"doc_id", "input_sha256", "reason", "source_ref", "source_sha256"}
    for r in records:
        require(type(r) is dict and set(r) == fields, "exposure_record_invalid")
        require(type(r["doc_id"]) is str and re.fullmatch(r"doc-[0-9a-f]{24}", r["doc_id"]) is not None and
                all(_digest(r[k]) for k in (*HASHES, "input_sha256", "source_sha256")), "exposure_record_hash_invalid")
        require(all(type(r[k]) is str and re.fullmatch(r"[a-z][a-z0-9_-]{2,79}", r[k]) is not None for k in FAMILIES), "exposure_family_invalid")
        require(type(r["reason"]) is str and r["reason"] in REASONS and type(r["source_ref"]) is str and
                0 < len(r["source_ref"]) <= 500 and bool(r["source_ref"].strip()), "exposure_source_invalid")
    require(len({r["doc_id"] for r in records}) == len(records) and records == sorted(records, key=lambda r: r["doc_id"]), "exposure_record_order_or_duplicates")
    return ledger


def extend_exposure_ledger(ledger, new_documents, *, expected_ledger_sha256, source_ref, source_sha256, reason):
    validate_exposure_ledger(ledger, expected_ledger_sha256=expected_ledger_sha256)
    fresh = build_exposure_ledger(new_documents, source_ref=source_ref, source_sha256=source_sha256, reason=reason)
    require(not {r["doc_id"] for r in ledger["records"]} & {r["doc_id"] for r in fresh["records"]}, "exposure_existing_record_overwrite")
    return _seal(ledger["records"] + fresh["records"])


def _semantic_edges(raw_links, docs):
    require(type(raw_links) in (list, tuple) and len(raw_links) <= 10000, "exposure_semantic_links_invalid")
    by_id = {d.input.doc_id: d for d in docs}
    edges, canonical = [], []
    for link in raw_links:
        require(type(link) is dict and set(link) == {"members", "reason"}, "exposure_semantic_link_invalid")
        members = link["members"]
        require(type(members) is list and len(members) >= 2 and all(type(m) is str and m in by_id for m in members) and
                len(set(members)) == len(members) and type(link["reason"]) is str and len(link["reason"].strip()) >= 10, "exposure_semantic_members_invalid")
        ids = sorted(members)
        edges.extend((ids[0], k) for k in ids[1:])
        canonical.append({"members": ids, "reason": link["reason"],
                          "input_hashes": {i: by_id[i].input_sha256 for i in ids}})
    canonical.sort(key=value_digest)
    require(len({value_digest(r) for r in canonical}) == len(canonical), "exposure_semantic_link_duplicate")
    return edges, canonical


def audit_exposure(raw_documents, ledger, *, expected_ledger_sha256, semantic_links=()):
    docs = sorted(validate_documents(raw_documents), key=lambda d: d.input.doc_id)
    validate_exposure_ledger(ledger, expected_ledger_sha256=expected_ledger_sha256)
    by_exposed = {r["doc_id"]: r for r in ledger["records"]}
    index = defaultdict(set)
    for r in ledger["records"]:
        for field in (*FAMILIES, *HASHES):
            index[(field, r[field])].add(r["doc_id"])
    matched, anchors = {}, {}
    for d in docs:
        reasons, parents = [], set()
        if d.input.doc_id in by_exposed:
            original = by_exposed[d.input.doc_id]
            require(d.input_sha256 == original["input_sha256"] and all(v == original[k] for k, v in _identities(d).items()), "exposure_same_id_changed")
            reasons.append("declared_doc_id")
            parents.add(d.input.doc_id)
        for field, value in _identities(d).items():
            if index[(field, value)]:
                reasons.append(field)
                parents.update(index[(field, value)])
        if reasons:
            matched[d.input.doc_id] = sorted(reasons)
            anchors[d.input.doc_id] = sorted(parents)
    duplicate = duplicate_audit(docs)
    edges = [(members[0], k) for members in duplicate["groups"].values() for k in members[1:]]
    semantic_edges, canonical = _semantic_edges(semantic_links, docs)
    groups = _groups(docs, edges + semantic_edges)
    blocked_groups = {group: members for group, members in groups.items() if any(i in matched for i in members)}
    forbidden = sorted(i for members in blocked_groups.values() for i in members)
    return {**FLAGS, "status": "declared_exposure_audit_not_blindness_certificate", "documents": len(docs),
            "ledger_sha256": expected_ledger_sha256, "ledger_records": len(ledger["records"]),
            "documents_sha256": value_digest([d.model_dump() for d in docs]),
            "direct_matches": matched, "matching_exposure_anchors": anchors,
            "groups": groups, "semantic_links": canonical, "duplicate_audit": duplicate,
            "forbidden_evaluation_ids": forbidden, "forbidden_evaluation_count": len(forbidden),
            "unmatched_count_not_proof_of_unexposed": len(docs) - len(forbidden),
            "ledger_records_absent_from_current_inputs": sorted(set(by_exposed) - {d.input.doc_id for d in docs}),
            "blindness_certified": False, "complete_access_history_certified": False,
            "limitations": ["Declared history and known fingerprints/links only; rewritten unlinked derivatives may escape.",
                            "No ACL or agent isolation is created. A hash authenticates bytes, not approval or truthful history."]}


def propose_exposure_safe_split(raw_documents, raw_answers, ledger, *, expected_ledger_sha256,
                                semantic_links=(), train_per_grade=200, evaluation_per_grade=50, seed=20260915):
    require(type(train_per_grade) is int and type(evaluation_per_grade) is int and train_per_grade > 0 and
            evaluation_per_grade > 0 and type(seed) is int, "exposure_split_sizes_invalid")
    docs = sorted(validate_documents(raw_documents), key=lambda d: d.input.doc_id)
    answers = validate_answers(docs, raw_answers)
    require(Counter(a.reference_grade for a in answers) == {g: train_per_grade + evaluation_per_grade for g in GRADES}, "exposure_grade_quota_invalid")
    audit = audit_exposure(docs, ledger, expected_ledger_sha256=expected_ledger_sha256, semantic_links=semantic_links)
    duplicate = audit["duplicate_audit"]
    require(not any(duplicate[k] for k in ("exact_pairs", "number_only_pairs", "known_fixture_body_matches")), "exposure_duplicate_or_fixture_blocked")
    groups = audit["groups"]
    keys = sorted(groups)
    forbidden = set(audit["forbidden_evaluation_ids"])
    labels = {a.doc_id: a.reference_grade for a in answers}
    forced = Counter(labels[i] for i in forbidden)
    require(all(forced[g] <= train_per_grade for g in GRADES), "exposure_train_capacity_exceeded")
    import numpy as np
    import scipy
    from scipy.optimize import Bounds, LinearConstraint, milp

    matrix = np.array([[sum(labels[i] == g for i in groups[k]) for k in keys] for g in GRADES], dtype=float)
    upper = np.array([0.0 if any(i in forbidden for i in groups[k]) else 1.0 for k in keys])
    cost = np.array([int(text_digest(f"{seed}:{k}")[:12], 16) / 16**12 for k in keys])
    result = milp(c=cost, integrality=np.ones(len(keys)), bounds=Bounds(np.zeros(len(keys)), upper),
                  constraints=LinearConstraint(matrix, evaluation_per_grade, evaluation_per_grade),
                  options={"time_limit": 30, "mip_rel_gap": 0.0})
    require(result.success and result.x is not None, "exposure_group_split_infeasible_or_timeout")
    evaluation = {i for k, chosen in zip(keys, result.x, strict=True) if chosen > 0.5 for i in groups[k]}
    require(not evaluation & forbidden, "exposure_solver_selected_forbidden")
    partitions = {d.input.doc_id: "evaluation" if d.input.doc_id in evaluation else "train" for d in docs}
    for part, count in (("train", train_per_grade), ("evaluation", evaluation_per_grade)):
        require(Counter(labels[i] for i, p in partitions.items() if p == part) == {g: count for g in GRADES}, "exposure_solver_quota_invalid")
    require(all(len({partitions[i] for i in members}) == 1 for members in groups.values()), "exposure_group_overlap")
    return {**FLAGS, "status": "exposure_constrained_split_proposal_only", "seed": seed,
            "solver": "scipy.optimize.milp", "scipy_version": scipy.__version__, "exposure_audit": audit,
            "partitions": partitions, "train_counts": {g: train_per_grade for g in GRADES},
            "evaluation_counts": {g: evaluation_per_grade for g in GRADES},
            "answers_sha256": value_digest([a.model_dump() for a in sorted(answers, key=lambda a: a.doc_id)]),
            "customer_size_contract_met": train_per_grade == 200 and evaluation_per_grade == 50,
            "blindness_certified": False, "release_authorized": False}
