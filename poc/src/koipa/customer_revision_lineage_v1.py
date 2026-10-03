"""Alias-repair lineage and declared exposure propagation; no new manuscripts.

Literal edit replay is not independent semantic approval of an alias dictionary.
Old exposure records remain intact; changed input versions receive new records.
"""
from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path

from koipa.customer_benchmark import FLAGS
from koipa.customer_eval_partition_v1 import (
    FAMILIES, audit_exposure, extend_exposure_ledger, validate_exposure_ledger,
)
from koipa.customer_guide_reference import POLICY_SHA256, validate_reference
from koipa.policy_facts import require, text_digest, value_digest

SCHEMA = "customer-identifier-revision-lineage-v1"
PARENT_MANIFEST_SHA256 = "fbcc96d012a3b87f4bd3fd6ba6fea9f62ef1d9c7f5e39b5a416fec7e91285a19"
PRIOR_LEDGER_SHA256 = "c92734b6b67154546581d1425351ed587f4506d6ca23cb966b69b09f2b9a9241"
LINEAGE_KEYS = set(FLAGS) | set(FAMILIES) | {
    "parent_doc_id", "child_doc_id", "parent_input_sha256", "child_input_sha256",
    "parent_body_sha256", "child_body_sha256", "parent_answer_sha256", "child_answer_sha256",
    "parent_evidence_sha256", "child_evidence_sha256", "policy_sha256", "context_sha256",
    "changed", "repair_kind", "edits", "new_document_count",
}
EDIT_KEYS = {"start", "end", "before", "after", "target_start", "target_end", "noun", "alias", "reason"}


def source_hashes():
    directory = Path(__file__).resolve().parent
    names = ("customer_revision_lineage_v1.py", "customer_eval_partition_v1.py", "customer_benchmark.py",
             "customer_guide_reference.py", "policy_facts.py")
    return {name: hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in names}


def _strict_flags(row):
    require(all(type(row.get(k)) is bool and row[k] is False for k in FLAGS), "revision_permission_invalid")


def _digest(value):
    return type(value) is str and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def replay_edits(parent_text, child_text, edits):
    """Only declared literal spans may change; numeric facts outside aliases stay."""
    require(type(edits) is list, "revision_edits_invalid")
    cursor, delta, pieces = 0, 0, []
    for edit in edits:
        require(type(edit) is dict and set(edit) == EDIT_KEYS, "revision_edit_fields_invalid")
        start, end = edit["start"], edit["end"]
        require(all(type(edit[k]) is int for k in ("start", "end", "target_start", "target_end")) and
                cursor <= start < end <= len(parent_text), "revision_edit_offset_invalid")
        require(all(type(edit[k]) is str and bool(edit[k].strip()) for k in ("before", "after", "noun", "alias", "reason")),
                "revision_edit_text_invalid")
        require(edit["before"] != edit["after"] and parent_text[start:end] == edit["before"], "revision_edit_source_mismatch")
        require(not any(c in edit["before"]+edit["after"] for c in "\r\n"), "revision_multiline_edit_forbidden")
        require(edit["target_start"] == start+delta and edit["target_end"] == start+delta+len(edit["after"]) and
                child_text[edit["target_start"]:edit["target_end"]] == edit["after"], "revision_edit_target_mismatch")
        pieces.extend((parent_text[cursor:start], edit["after"]))
        delta += len(edit["after"])-(end-start)
        cursor = end
    pieces.append(parent_text[cursor:])
    require("".join(pieces) == child_text, "revision_unlisted_body_change")
    require(re.findall(r"\d+(?:\.\d+)?", parent_text) == re.findall(r"\d+(?:\.\d+)?", child_text),
            "revision_numeric_facts_changed")


def _rebound_claims(parent, child, edits):
    def shifted(offset):
        require(not any(e["start"] < offset < e["end"] for e in edits), "revision_claim_boundary_cuts_edit")
        return offset+sum(len(e["after"])-(e["end"]-e["start"]) for e in edits if e["end"] <= offset)
    require(len(parent.claims) == len(child.claims), "revision_claim_count_changed")
    for source, target in zip(parent.claims, child.claims, strict=True):
        expected = source.model_dump()
        expected["start"], expected["end"] = shifted(source.start), shifted(source.end)
        quote = child.input.text[expected["start"]:expected["end"]]
        expected["quote"], expected["sha256"] = quote, text_digest(quote)
        if source.claim == source.quote:
            expected["claim"] = quote
        require(expected == target.model_dump(), "revision_claim_rebinding_mismatch")


def audit_revision(original_records, original_answers, revised_records, revised_answers, lineage, *,
        original_evidence, revised_evidence, panel_parent_ids, ledger, expected_ledger_sha256,
        parent_manifest_sha256, source_ref, source_sha256, semantic_links=(),
        expected_document_count=260, expected_panel_count=64, expected_previous_groups=236):
    """Replace a fixed panel once, retain old history, extend changed views only."""
    require(all(type(n) is int and n > 0 for n in (expected_document_count, expected_panel_count, expected_previous_groups)) and
            expected_panel_count <= expected_document_count, "revision_count_contract_invalid")
    require(parent_manifest_sha256 == PARENT_MANIFEST_SHA256 and _digest(source_sha256), "revision_parent_or_source_pin_invalid")
    require(type(source_ref) is str and 0 < len(source_ref) <= 500 and bool(source_ref.strip()), "revision_source_ref_invalid")
    validate_exposure_ledger(ledger, expected_ledger_sha256=expected_ledger_sha256)
    original = validate_reference(original_records, original_answers, original_evidence)
    revised = validate_reference(revised_records, revised_answers, revised_evidence)
    require(len(original) == len(revised) == expected_document_count, "revision_active_document_count_invalid")
    old = {d.input.doc_id: d for d in original}
    new = {d.input.doc_id: d for d in revised}
    old_answers = {a["doc_id"]: a for a in original_answers}
    new_answers = {a["doc_id"]: a for a in revised_answers}
    old_evidence = {e["doc_id"]: e for e in original_evidence}
    new_evidence = {e["doc_id"]: e for e in revised_evidence}
    require(type(panel_parent_ids) in (list, tuple) and len(panel_parent_ids) == expected_panel_count and
        all(type(k) is str and k in old for k in panel_parent_ids) and len(set(panel_parent_ids)) == expected_panel_count,
        "revision_panel_parent_ids_invalid")
    panel = set(panel_parent_ids)
    require(type(lineage) is list and len(lineage) == expected_panel_count, "revision_lineage_coverage_invalid")
    require(all(type(row) is dict and set(row) == LINEAGE_KEYS for row in lineage), "revision_lineage_fields_invalid")
    for row in lineage:
        _strict_flags(row)
    require(all(type(row["parent_doc_id"]) is str and type(row["child_doc_id"]) is str for row in lineage),
            "revision_lineage_ids_invalid")
    require({r["parent_doc_id"] for r in lineage} == panel and
            len({r["parent_doc_id"] for r in lineage}) == len({r["child_doc_id"] for r in lineage}) == expected_panel_count,
            "revision_lineage_ids_invalid")
    mapping = {r["parent_doc_id"]: r["child_doc_id"] for r in lineage}
    require(set(new) == (set(old)-panel) | set(mapping.values()), "revision_active_replacement_invalid")
    require(len(ledger["records"]) == expected_document_count and {r["doc_id"] for r in ledger["records"]} == set(old),
            "revision_original_ledger_coverage_invalid")
    prior = audit_exposure(original_records, ledger, expected_ledger_sha256=expected_ledger_sha256, semantic_links=semantic_links)
    require(prior["forbidden_evaluation_count"] == expected_document_count and len(prior["groups"]) == expected_previous_groups,
            "revision_previous_exposure_or_groups_invalid")
    changed_ids, unchanged_ids, edits_count = [], [], 0
    for row in lineage:
        parent, child = old[row["parent_doc_id"]], new[row["child_doc_id"]]
        pid, cid = parent.input.doc_id, child.input.doc_id
        require(type(row["changed"]) is bool and row["repair_kind"] == "identifier_alias_repair" and
            type(row["new_document_count"]) is int and row["new_document_count"] == 0, "revision_role_invalid")
        actual_changed = parent.input.text != child.input.text
        require(row["changed"] == actual_changed, "revision_changed_flag_mismatch")
        require((cid != pid and cid not in old) if actual_changed else cid == pid, "revision_child_id_reused_or_changed")
        if actual_changed:
            identity = "doc-"+value_digest({"text": child.input.text, "context": [c.model_dump() for c in child.input.context]})[:24]
            require(cid == identity, "revision_child_content_id_mismatch")
            changed_ids.append(cid)
        else:
            unchanged_ids.append(cid)
        expected_hashes = {
            "parent_input_sha256": parent.input_sha256, "child_input_sha256": child.input_sha256,
            "parent_body_sha256": text_digest(parent.input.text), "child_body_sha256": text_digest(child.input.text),
            "parent_answer_sha256": value_digest(old_answers[pid]), "child_answer_sha256": value_digest(new_answers[cid]),
            "parent_evidence_sha256": value_digest(old_evidence[pid]), "child_evidence_sha256": value_digest(new_evidence[cid]),
            "policy_sha256": POLICY_SHA256, "context_sha256": value_digest([c.model_dump() for c in parent.input.context]),
        }
        require(all(row[k] == v for k, v in expected_hashes.items()), "revision_sidecar_hash_mismatch")
        require(parent.input.context == child.input.context, "revision_context_changed")
        for key in FAMILIES:
            require(row[key] == getattr(parent, key) == getattr(child, key), "revision_family_changed")
        parent_meta, child_meta = parent.model_dump(), child.model_dump()
        for value in (parent_meta, child_meta):
            for key in ("input", "input_sha256", "claims"):
                value.pop(key)
        require(parent_meta == child_meta, "revision_document_metadata_changed")
        for old_values, new_values, exempt, code in (
            (old_answers[pid], new_answers[cid], {"doc_id", "input_sha256"}, "revision_answer_changed"),
            (old_evidence[pid], new_evidence[cid], {"doc_id", "input_sha256", "body_sha256"}, "revision_evidence_facts_changed"),
        ):
            require({k: v for k, v in old_values.items() if k not in exempt} ==
                    {k: v for k, v in new_values.items() if k not in exempt}, code)
        replay_edits(parent.input.text, child.input.text, row["edits"])
        require(bool(row["edits"]) == actual_changed, "revision_edit_presence_mismatch")
        _rebound_claims(parent, child, row["edits"])
        edits_count += len(row["edits"])
    for doc_id in set(old)-panel:
        require(old[doc_id].model_dump() == new[doc_id].model_dump() and old_answers[doc_id] == new_answers[doc_id] and
                old_evidence[doc_id] == new_evidence[doc_id], "revision_nonpanel_record_changed")
    links = [{"members": [mapping.get(i, i) for i in link["members"]], "reason": link["reason"]} for link in semantic_links]
    extended = (extend_exposure_ledger(ledger, [new[k].model_dump() for k in sorted(changed_ids)],
        expected_ledger_sha256=expected_ledger_sha256, source_ref=source_ref, source_sha256=source_sha256,
        reason="development_authoring") if changed_ids else copy.deepcopy(ledger))
    old_rows = {r["doc_id"]: r for r in ledger["records"]}
    require(all(r == old_rows[r["doc_id"]] for r in extended["records"] if r["doc_id"] in old_rows),
            "revision_old_exposure_record_changed")
    exposure = audit_exposure(revised_records, extended, expected_ledger_sha256=extended["ledger_sha256"], semantic_links=links)
    require(exposure["forbidden_evaluation_count"] == expected_document_count and
            len(exposure["groups"]) == expected_previous_groups, "revision_active_exposure_or_groups_changed")
    retired = sorted(pid for pid, cid in mapping.items() if pid != cid)
    require(exposure["ledger_records_absent_from_current_inputs"] == retired and
            len(extended["records"]) == len(ledger["records"])+len(changed_ids), "revision_exposure_version_accounting_invalid")
    return {**FLAGS, "schema_version": SCHEMA, "status": "diagnostic_revision_lineage_not_adopted",
        "parent_manifest_sha256": parent_manifest_sha256, "revision_source_sha256": source_sha256,
        "previous_ledger_sha256": expected_ledger_sha256, "active_documents": len(revised),
        "active_documents_role": "proposed_diagnostic_views_not_authoritative_corpus",
        "adoption_allowed": False, "authoritative_parent_replaced": False,
        "repair_parent_documents": len(panel), "changed_document_versions": len(changed_ids),
        "unchanged_panel_documents": len(unchanged_ids), "literal_edit_spans": edits_count,
        "new_manuscripts": 0, "previous_exposure_document_versions": len(ledger["records"]),
        "physical_exposure_records_as_document_versions": len(extended["records"]),
        "active_documents_sha256": value_digest([d.model_dump() for d in sorted(revised, key=lambda d: d.input.doc_id)]),
        "active_answers_sha256": value_digest(sorted(revised_answers, key=lambda a: a["doc_id"])),
        "lineage_sha256": value_digest(sorted(lineage, key=lambda r: r["parent_doc_id"])),
        "parent_to_active_id": dict(sorted(mapping.items())), "retired_parent_ids": retired,
        "retired_parent_ids_scope": "absent_from_proposed_view_only_not_retired_from_authoritative_corpus",
        "parents_absent_from_diagnostic_view": retired,
        "changed_active_ids": sorted(changed_ids), "unchanged_panel_ids": sorted(unchanged_ids),
        "semantic_links_remapped": links, "previous_groups": len(prior["groups"]),
        "active_groups": len(exposure["groups"]), "exposure_ledger": extended, "exposure_audit": exposure,
        "source_files_sha256": source_hashes(), "all_active_documents_forbidden_final_evaluation": True,
        "alias_semantics_independently_certified_here": False, "complete_access_history_certified": False,
        "model_forward_executed": False, "training_performed": False, "customer_accuracy_measured": False}
