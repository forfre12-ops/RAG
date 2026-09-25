"""Bind proposed clarification versions to source reviews and declared exposure.

Literal changes, authored assumptions and policy arithmetic are checkable here.
Semantic equivalence, reviewer approval and permission to use data are not.
"""
from __future__ import annotations

import hashlib
import re
from pathlib import Path

from koipa.customer_benchmark import FLAGS, validate_documents
from koipa.customer_eval_partition_v1 import FAMILIES, audit_exposure, extend_exposure_ledger, validate_exposure_ledger
from koipa.customer_reference_audit_v1 import POLICY_SHA256, audit_reference
from koipa.policy_facts import require, text_digest, value_digest

SCHEMA = "customer-clarification-audit-v1"
PARENT_MANIFEST_SHA256 = "7e8ea7552e272f5391f1d95113176f955f8828352d38f45cfc24b979eb3c8f95"
ADOPTION_MANIFEST_SHA256 = "7c1d0f2f7004e33fd7ac7225fa1788f908973146725562dcfb2a34f32b326ce1"
ADOPTION_LEDGER_SHA256 = "1f9ceca8e52e1487d8f5e54eedc5dac7b10e8038e984c36f4068eb63b8a6a694"
EXPOSURE_LEDGER_SHA256 = "ec4e33107d387e04244d1cdb1bf4c952d1abc19e8a4440dd2001c918710d438f"
LINEAGE_FIELDS = set(FLAGS) | set(FAMILIES) | {
    "parent_doc_id", "child_doc_id", "parent_input_sha256", "child_input_sha256",
    "parent_body_sha256", "child_body_sha256", "parent_answer_sha256", "child_answer_sha256",
    "parent_evidence_sha256", "child_evidence_sha256", "policy_sha256", "context_sha256",
    "repair_kind", "new_document_count", "edits", "additional_assumptions", "excluded_scope",
    "claim_changes", "rationale_change", "remaining_hold",
}
EDIT_FIELDS = {"start", "end", "before", "after", "target_start", "target_end", "before_sha256", "after_sha256"}
NOTE_FIELDS = set(FLAGS) | {"parent_doc_id", "child_doc_id", "review_kind", "decision_reason", "remaining_issues", "remaining_hold"}
ASSUMPTION_FIELDS = {"statement", "origin", "quote", "start", "end", "sha256"}


def source_hashes():
    directory = Path(__file__).resolve().parent
    names = ("customer_clarification_audit_v1.py", "customer_reference_audit_v1.py", "customer_eval_partition_v1.py",
             "customer_benchmark.py", "policy_facts.py")
    return {"src/koipa/"+name: hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in names}


def _flags(row):
    require(all(type(row.get(k)) is bool and row[k] is False for k in FLAGS), "clarification_permission_invalid")


def _note(value, minimum=10):
    require(type(value) is str and minimum <= len(value.strip()) <= 5000 and any(c.isalpha() for c in value),
            "clarification_note_required")


def replay_edits(parent_text, child_text, edits):
    """Replay every declared literal insertion/deletion/replacement, no truncation."""
    require(type(edits) is list and 0 < len(edits) <= 100, "clarification_edits_required")
    cursor, delta, pieces, last_start = 0, 0, [], -1
    for edit in edits:
        require(type(edit) is dict and set(edit) == EDIT_FIELDS, "clarification_edit_fields_invalid")
        require(all(type(edit[k]) is int for k in ("start", "end", "target_start", "target_end")),
                "clarification_edit_offset_invalid")
        start, end = edit["start"], edit["end"]
        require(cursor <= start <= end <= len(parent_text) and start > last_start, "clarification_edit_offset_invalid")
        require(type(edit["before"]) is str and type(edit["after"]) is str and edit["before"] != edit["after"] and
                parent_text[start:end] == edit["before"] and text_digest(edit["before"]) == edit["before_sha256"] and
                text_digest(edit["after"]) == edit["after_sha256"], "clarification_edit_source_or_hash_mismatch")
        require(edit["target_start"] == start+delta and edit["target_end"] == start+delta+len(edit["after"]) and
                child_text[edit["target_start"]:edit["target_end"]] == edit["after"], "clarification_edit_target_mismatch")
        pieces.extend((parent_text[cursor:start], edit["after"]))
        delta += len(edit["after"])-(end-start)
        cursor, last_start = end, start
    pieces.append(parent_text[cursor:])
    require("".join(pieces) == child_text, "clarification_unlisted_body_change")


def _authored_scope(items, text, *, required):
    require(type(items) is list and int(required) <= len(items) <= 30, "clarification_authored_scope_required")
    for item in items:
        require(type(item) is dict and set(item) == ASSUMPTION_FIELDS, "clarification_authored_scope_fields_invalid")
        require(item["origin"] == "authored_for_revised_synthetic_edition", "clarification_assumption_origin_invalid")
        _note(item["statement"])
        require(type(item["start"]) is int and type(item["end"]) is int and
                0 <= item["start"] < item["end"] <= len(text) and type(item["quote"]) is str and
                bool(item["quote"].strip()) and text[item["start"]:item["end"]] == item["quote"] and
                text_digest(item["quote"]) == item["sha256"], "clarification_authored_scope_binding_invalid")
    require(len({value_digest(item) for item in items}) == len(items), "clarification_duplicate_authored_scope")


def _changes(parent, child, row, old_answer, answer, old_evidence, evidence):
    pid, cid = parent["input"]["doc_id"], child["input"]["doc_id"]
    old_text, text = parent["input"]["text"], child["input"]["text"]
    require(old_text != text and parent["input_sha256"] != child["input_sha256"], "clarification_body_not_changed")
    require(child["input"]["context"] == parent["input"]["context"], "clarification_context_changed")
    require(cid == "doc-"+value_digest({"text": text, "context": child["input"]["context"]})[:24],
            "clarification_child_content_id_invalid")
    bindings = {"parent_input_sha256": parent["input_sha256"], "child_input_sha256": child["input_sha256"],
        "parent_body_sha256": text_digest(old_text), "child_body_sha256": text_digest(text),
        "parent_answer_sha256": value_digest(old_answer), "child_answer_sha256": value_digest(answer),
        "parent_evidence_sha256": value_digest(old_evidence), "child_evidence_sha256": value_digest(evidence),
        "context_sha256": value_digest(parent["input"]["context"]), "policy_sha256": POLICY_SHA256}
    require(all(row[k] == v for k, v in bindings.items()), "clarification_lineage_binding_mismatch")
    require(all(row[k] == parent[k] == child[k] for k in FAMILIES), "clarification_family_changed")
    for before, after, exempt in (
        (parent, child, {"input", "input_sha256", "claims"}),
        (old_answer, answer, {"doc_id", "input_sha256", "evidence_names"}),
        (old_evidence, evidence, {"doc_id", "input_sha256", "body_sha256", "rationale"}),
    ):
        require({k: v for k, v in before.items() if k not in exempt} ==
                {k: v for k, v in after.items() if k not in exempt}, "clarification_undeclared_metadata_change")
    replay_edits(old_text, text, row["edits"])
    old_claims = {c["name"]: c for c in parent["claims"]}
    claims = {c["name"]: c for c in child["claims"]}
    changes = [{"name": name, "before": old_claims.get(name), "after": claims.get(name)}
               for name in sorted(set(old_claims) | set(claims)) if old_claims.get(name) != claims.get(name)]
    require(type(row["claim_changes"]) is list and all(type(c) is dict and set(c) == {"name", "before", "after"}
            and type(c["name"]) is str for c in row["claim_changes"]), "clarification_claim_change_fields_invalid")
    require(sorted(row["claim_changes"], key=lambda c: c["name"]) == changes, "clarification_claim_changes_mismatch")
    require(answer["evidence_names"] == [c["name"] for c in child["claims"]], "clarification_answer_claim_coverage_invalid")
    require(type(row["rationale_change"]) is dict and row["rationale_change"] ==
            {"before": old_evidence["rationale"], "after": evidence["rationale"]}, "clarification_rationale_change_mismatch")
    _note(evidence["rationale"], 40)
    _authored_scope(row["additional_assumptions"], text, required=False)
    _authored_scope(row["excluded_scope"], text, required=False)
    require(row["parent_doc_id"] == pid and row["child_doc_id"] == cid, "clarification_lineage_identity_invalid")


def audit_clarification(original_records, original_answers, original_evidence, revisions, *, adoption_ledger,
        parent_manifest_sha256, adoption_manifest_sha256, expected_adoption_ledger_sha256,
        ledger, expected_ledger_sha256, source_ref, source_sha256, semantic_links=(),
        expected_document_count=260, expected_revision_count=11, expected_previous_versions=276, expected_previous_groups=236):
    """Audit proposed versions; author's remaining_hold never becomes acceptance."""
    require(parent_manifest_sha256 == PARENT_MANIFEST_SHA256 and adoption_manifest_sha256 == ADOPTION_MANIFEST_SHA256 and
            expected_adoption_ledger_sha256 == ADOPTION_LEDGER_SHA256 and expected_ledger_sha256 == EXPOSURE_LEDGER_SHA256,
            "clarification_fixed_pin_invalid")
    require(type(source_sha256) is str and re.fullmatch(r"[0-9a-f]{64}", source_sha256) is not None,
            "clarification_source_pin_invalid")
    require(all(type(n) is int and n > 0 for n in (expected_document_count, expected_revision_count,
            expected_previous_versions, expected_previous_groups)) and expected_revision_count <= expected_document_count,
            "clarification_count_contract_invalid")
    require(type(revisions) is dict and set(revisions) == {"records", "answers", "evidence", "lineage", "review_notes"},
            "clarification_payload_fields_invalid")
    before = value_digest([original_records, original_answers, original_evidence, revisions, adoption_ledger, ledger, semantic_links])
    original_policy = audit_reference(original_records, original_answers, original_evidence)
    independent = audit_reference(revisions["records"], revisions["answers"], revisions["evidence"])
    original, revised = validate_documents(original_records), validate_documents(revisions["records"])
    require(len(original) == len(revised) == expected_document_count, "clarification_document_count_invalid")
    old = {r["input"]["doc_id"]: r for r in original_records}
    new = {r["input"]["doc_id"]: r for r in revisions["records"]}
    oa, na = ({r["doc_id"]: r for r in items} for items in (original_answers, revisions["answers"]))
    oe, ne = ({r["doc_id"]: r for r in items} for items in (original_evidence, revisions["evidence"]))
    require(type(adoption_ledger) is dict and adoption_ledger.get("ledger_sha256") == expected_adoption_ledger_sha256 and
            value_digest({k: v for k, v in adoption_ledger.items() if k != "ledger_sha256"}) == expected_adoption_ledger_sha256,
            "clarification_adoption_ledger_pin_invalid")
    _flags(adoption_ledger)
    require(adoption_ledger["source_manifest_sha256"] == parent_manifest_sha256 and
            adoption_ledger["policy_sha256"] == POLICY_SHA256 and
            adoption_ledger["documents_sha256"] == value_digest([d.model_dump() for d in sorted(original, key=lambda d: d.input.doc_id)]) and
            adoption_ledger["answers_sha256"] == value_digest(sorted(original_answers, key=lambda r: r["doc_id"])) and
            adoption_ledger["evidence_sha256"] == value_digest(sorted(original_evidence, key=lambda r: r["doc_id"])),
            "clarification_adoption_source_binding_invalid")
    held = {r["doc_id"] for r in adoption_ledger["decisions"] if r["conditional_reference_decision"] == "hold" and r["source_disposition"] == "revise"}
    require(len(held) == expected_revision_count and len(adoption_ledger["decisions"]) == expected_document_count and
            {r["doc_id"] for r in adoption_ledger["decisions"]} == set(old) and
            set(adoption_ledger["internal_reference_held_ids"]) == held and
            set(adoption_ledger["internal_reference_accepted_ids"]) == set(old)-held and
            all(r["benchmark_decision"] == "hold" for r in adoption_ledger["decisions"]), "clarification_adoption_target_invalid")
    lineage, notes = revisions["lineage"], revisions["review_notes"]
    require(type(lineage) is list and type(notes) is list and len(lineage) == len(notes) == expected_revision_count,
            "clarification_review_coverage_invalid")
    require(all(type(r) is dict and set(r) == LINEAGE_FIELDS and type(r["parent_doc_id"]) is str and
                type(r["child_doc_id"]) is str for r in lineage), "clarification_lineage_fields_invalid")
    mapping = {r["parent_doc_id"]: r["child_doc_id"] for r in lineage}
    require(set(mapping) == held and len(set(mapping.values())) == expected_revision_count and
            not set(mapping.values()) & set(old) and set(new) == (set(old)-held) | set(mapping.values()),
            "clarification_parent_child_coverage_invalid")
    require(all(type(n) is dict and set(n) == NOTE_FIELDS and type(n["parent_doc_id"]) is str for n in notes),
            "clarification_note_fields_invalid")
    note_map = {n["parent_doc_id"]: n for n in notes}
    require(len(note_map) == expected_revision_count and set(note_map) == held, "clarification_note_coverage_invalid")
    for row in lineage:
        _flags(row)
        require(row["repair_kind"] == "synthetic_scope_clarification" and type(row["new_document_count"]) is int and
                row["new_document_count"] == 0 and type(row["remaining_hold"]) is bool, "clarification_role_invalid")
        pid, cid = row["parent_doc_id"], row["child_doc_id"]
        _changes(old[pid], new[cid], row, oa[pid], na[cid], oe[pid], ne[cid])
        note = note_map[pid]
        _flags(note)
        require(note["child_doc_id"] == cid and note["review_kind"] == "ai_authored_clarification_not_independent_approval" and
                type(note["remaining_hold"]) is bool and note["remaining_hold"] == row["remaining_hold"], "clarification_note_authority_invalid")
        _note(note["decision_reason"], 40)
        require(type(note["remaining_issues"]) is list and len(note["remaining_issues"]) <= 30 and
                bool(note["remaining_issues"]) == note["remaining_hold"], "clarification_remaining_issues_invalid")
        for issue in note["remaining_issues"]:
            _note(issue)
    for key in set(old)-held:
        require(old[key] == new[key] and oa[key] == na[key] and oe[key] == ne[key], "clarification_accepted_source_changed")
    validate_exposure_ledger(ledger, expected_ledger_sha256=expected_ledger_sha256)
    require(len(ledger["records"]) == expected_previous_versions and set(old) <= {r["doc_id"] for r in ledger["records"]} and
            not set(mapping.values()) & {r["doc_id"] for r in ledger["records"]}, "clarification_previous_exposure_invalid")
    prior = audit_exposure(original_records, ledger, expected_ledger_sha256=expected_ledger_sha256, semantic_links=semantic_links)
    require(prior["forbidden_evaluation_count"] == expected_document_count and len(prior["groups"]) == expected_previous_groups,
            "clarification_prior_group_or_exposure_invalid")
    links = [{"members": [mapping.get(i, i) for i in link["members"]], "reason": link["reason"]} for link in semantic_links]
    links += [{"members": [mapping.get(i, i) for i in members],
               "reason": "Preserved source exposure group before clarification: "+group}
              for group, members in prior["groups"].items() if len(members) > 1]
    extended = extend_exposure_ledger(ledger, [new[cid] for cid in sorted(mapping.values())],
        expected_ledger_sha256=expected_ledger_sha256, source_ref=source_ref, source_sha256=source_sha256, reason="development_authoring")
    old_rows = {r["doc_id"]: r for r in ledger["records"]}
    require(all(r == old_rows[r["doc_id"]] for r in extended["records"] if r["doc_id"] in old_rows) and
            len(extended["records"]) == expected_previous_versions+expected_revision_count, "clarification_exposure_history_changed")
    exposure = audit_exposure(revisions["records"], extended, expected_ledger_sha256=extended["ledger_sha256"], semantic_links=links)
    group_of = {i: group for group, members in exposure["groups"].items() for i in members}
    require(exposure["forbidden_evaluation_count"] == expected_document_count and
            all(len({group_of[mapping.get(i, i)] for i in members}) == 1 for members in prior["groups"].values()) and
            len(exposure["groups"]) <= len(prior["groups"]), "clarification_previous_group_split_or_exposure_lost")
    require(set(exposure["ledger_records_absent_from_current_inputs"]) == set(old_rows)-set(new),
            "clarification_exposure_absence_accounting_invalid")
    require(value_digest([original_records, original_answers, original_evidence, revisions, adoption_ledger, ledger, semantic_links]) == before,
            "clarification_input_mutated")
    return {**FLAGS, "schema_version": SCHEMA, "status": "proposed_clarification_versions_not_reference_adoption",
        "parent_manifest_sha256": parent_manifest_sha256, "adoption_manifest_sha256": adoption_manifest_sha256,
        "adoption_ledger_sha256": expected_adoption_ledger_sha256, "previous_ledger_sha256": expected_ledger_sha256,
        "revision_source_sha256": source_sha256, "revisions_sha256": value_digest(revisions),
        "active_documents": len(new), "active_documents_role": "proposed_clarification_view_not_authoritative_corpus",
        "revision_parent_documents": len(held), "changed_document_versions": len(mapping), "unchanged_source_documents": len(old)-len(held),
        "new_manuscripts": 0, "authoritative_parent_replaced": False, "internal_reference_adoption_changed": False,
        "author_reported_remaining_conditional_holds": sum(n["remaining_hold"] for n in notes),
        "author_notes_are_independent_acceptance": False, "semantic_equivalence_certified": False,
        "customer_truth_certified": False, "benchmark_training_adopted": 0, "benchmark_evaluation_adopted": 0,
        "benchmark_candidates_held": len(new), "model_forward_executed": False, "training_performed": False,
        "previous_exposure_document_versions": len(old_rows), "physical_exposure_records_as_document_versions": len(extended["records"]),
        "previous_groups": len(prior["groups"]), "active_groups": len(exposure["groups"]), "previous_group_splits": 0,
        "parent_to_active_id": dict(sorted(mapping.items())), "semantic_links_remapped": links,
        "exposure_ledger": extended, "exposure_audit": exposure, "independent_policy_audit": independent,
        "original_policy_cases_checked": original_policy["passed"], "source_files_sha256": source_hashes(),
        "all_active_documents_forbidden_final_evaluation": True,
        "limitations": ["Literal diff and quote bindings do not prove semantic equivalence or reviewer truth.",
            "Additional assumptions describe a revised fictional edition, not observed facts in its parent.",
            "Independent arithmetic follows the same fixed policy; customer truth and adoption remain outside this check."]}
