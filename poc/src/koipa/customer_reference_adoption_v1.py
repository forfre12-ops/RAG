"""Validate explicit internal AI review records, not semantic truth or training use.

Quotations bind written review notes to supplied sources; they cannot prove that
an agent read, understood or correctly reviewed the document. No automatic vote.
"""
from __future__ import annotations

import copy
import hashlib
import re
from collections import Counter
from pathlib import Path

from koipa.customer_benchmark import FLAGS, GRADES
from koipa.customer_guide_reference import POLICY_SHA256, validate_reference
from koipa.policy_facts import require, text_digest, value_digest

SOURCE_MANIFEST_SHA256 = "7e8ea7552e272f5391f1d95113176f955f8828352d38f45cfc24b979eb3c8f95"
DECISION_SCHEMA = "customer-source-disposition-v1"
LEDGER_SCHEMA = "customer-internal-reference-adoption-ledger-v1"
DECISION_FIELDS = set(FLAGS) | {
    "schema_version", "source_manifest_sha256", "doc_id", "input_sha256", "body_sha256",
    "answer_sha256", "evidence_sha256", "policy_sha256", "reference_grade", "source_disposition",
    "conditional_reference_decision", "benchmark_decision", "reviewer_kind", "review_scope", "source_unchanged",
    "body_review_note", "context_review_note", "decision_reason", "required_action",
    "body_evidence", "context_evidence", "findings",
}
DISPOSITIONS = {"keep": "accept", "revise": "hold", "exclude": "reject"}
CONTEXT_REVIEW_NAMES = {"reader_scope", "impact_description", "management_controls"}


def source_hashes():
    directory = Path(__file__).resolve().parent
    names = ("customer_reference_adoption_v1.py", "customer_benchmark.py", "customer_guide_reference.py", "policy_facts.py")
    return {"src/koipa/"+name: hashlib.sha256((directory/name).read_bytes()).hexdigest() for name in names}


def _note(value, minimum, code):
    require(type(value) is str and minimum <= len(value.strip()) <= 5000 and
            any(c.isalpha() for c in value), code)


def _validate_review(row, doc, answer, evidence, hard_hold):
    require(type(row) is dict and set(row) == DECISION_FIELDS, "adoption_decision_fields_invalid")
    require(all(type(row[k]) is bool and row[k] is False for k in FLAGS), "adoption_permission_invalid")
    require(row["schema_version"] == DECISION_SCHEMA and row["reviewer_kind"] == "ai_internal_review" and
            row["review_scope"] == "body_and_synthetic_context" and type(row["source_unchanged"]) is bool and
            row["source_unchanged"] is True, "adoption_review_authority_invalid")
    expected = {"source_manifest_sha256": SOURCE_MANIFEST_SHA256, "doc_id": doc.input.doc_id,
        "input_sha256": doc.input_sha256, "body_sha256": text_digest(doc.input.text), "answer_sha256": value_digest(answer),
        "evidence_sha256": value_digest(evidence), "policy_sha256": POLICY_SHA256, "reference_grade": answer["reference_grade"]}
    require(all(row[k] == v for k, v in expected.items()), "adoption_source_binding_mismatch")
    source, conditional, benchmark = (row[k] for k in ("source_disposition", "conditional_reference_decision", "benchmark_decision"))
    require(type(source) is str and source in DISPOSITIONS and conditional == DISPOSITIONS[source], "adoption_decisions_conflict")
    require(type(benchmark) is str and benchmark in {"hold", "eligible"}, "adoption_benchmark_decision_invalid")
    require(benchmark != "eligible" or conditional == "accept", "adoption_benchmark_reference_conflict")
    require(doc.input.doc_id not in hard_hold or benchmark == "hold", "adoption_existing_benchmark_hold_bypassed")
    for name in ("body_review_note", "context_review_note", "decision_reason"):
        _note(row[name], 40, "adoption_substantive_review_note_required")
    _note(row["required_action"], 10, "adoption_required_action_missing")
    require(row["body_review_note"].strip() != row["context_review_note"].strip(), "adoption_body_context_review_not_separated")
    body_quotes = row["body_evidence"]
    require(type(body_quotes) is list and 2 <= len(body_quotes) <= 12, "adoption_body_evidence_required")
    spans = set()
    for quote in body_quotes:
        require(type(quote) is dict and set(quote) == {"quote", "start", "end", "sha256"}, "adoption_body_evidence_fields_invalid")
        start, end = quote["start"], quote["end"]
        require(type(start) is int and type(end) is int and 0 <= start < end <= len(doc.input.text) and
            type(quote["quote"]) is str and bool(quote["quote"].strip()) and doc.input.text[start:end] == quote["quote"] and
            quote["sha256"] == text_digest(quote["quote"]), "adoption_body_quote_binding_invalid")
        require((start, end) not in spans, "adoption_duplicate_body_evidence")
        spans.add((start, end))
    require(len({q["quote"] for q in body_quotes}) == len(body_quotes) and
            any(any(c.isalpha() for c in q["quote"]) for q in body_quotes), "adoption_distinct_body_review_evidence_required")
    contexts = {c.name: c.value for c in doc.input.context}
    context_quotes = row["context_evidence"]
    require(type(context_quotes) is list and 3 <= len(context_quotes) <= len(contexts), "adoption_context_evidence_required")
    names = set()
    for quote in context_quotes:
        require(type(quote) is dict and set(quote) == {"name", "quote", "sha256"}, "adoption_context_evidence_fields_invalid")
        name = quote["name"]
        require(type(name) is str and name in contexts and name not in names, "adoption_context_evidence_name_invalid")
        require(value_digest(quote["quote"]) == value_digest(contexts[name]) and
                quote["sha256"] == value_digest(contexts[name]), "adoption_context_quote_binding_invalid")
        names.add(name)
    require(CONTEXT_REVIEW_NAMES <= names, "adoption_core_context_not_reviewed")
    findings = row["findings"]
    require(type(findings) is list and len(findings) <= 30, "adoption_findings_invalid")
    scopes = []
    for finding in findings:
        require(type(finding) is dict and set(finding) == {"code", "scope", "reason"} and
            type(finding["code"]) is str and re.fullmatch(r"[a-z][a-z0-9_-]{2,79}", finding["code"]) is not None,
            "adoption_finding_fields_invalid")
        require(type(finding["scope"]) is str and finding["scope"] in {"conditional_reference", "benchmark", "both"},
                "adoption_finding_scope_invalid")
        _note(finding["reason"], 20, "adoption_finding_reason_required")
        scopes.append(finding["scope"])
    require(conditional == "accept" or any(s in {"conditional_reference", "both"} for s in scopes),
            "adoption_unresolved_reference_finding_required")
    require(conditional != "accept" or not any(s in {"conditional_reference", "both"} for s in scopes),
            "adoption_accept_with_open_reference_finding")
    require(benchmark != "eligible" or not any(s in {"benchmark", "both"} for s in scopes),
            "adoption_eligible_with_open_benchmark_finding")
    require(benchmark != "hold" or any(s in {"benchmark", "both"} for s in scopes),
            "adoption_benchmark_hold_finding_required")


def build_adoption_ledger(records, answers, evidence, decisions, *, source_manifest_sha256,
        hard_hold_benchmark_ids, expected_count=260, expected_hard_hold_count=64):
    """Aggregate explicit per-document AI decisions, never manufacture a decision."""
    require(source_manifest_sha256 == SOURCE_MANIFEST_SHA256, "adoption_source_manifest_pin_invalid")
    require(type(expected_count) is int and expected_count > 0 and type(expected_hard_hold_count) is int and
            0 <= expected_hard_hold_count <= expected_count, "adoption_count_contract_invalid")
    docs = validate_reference(records, answers, evidence)
    require(len(docs) == expected_count, "adoption_source_count_invalid")
    by_id = {d.input.doc_id: d for d in docs}
    answer_map = {r["doc_id"]: r for r in answers}
    evidence_map = {r["doc_id"]: r for r in evidence}
    require(type(hard_hold_benchmark_ids) in (list, tuple) and len(hard_hold_benchmark_ids) == expected_hard_hold_count and
            all(type(i) is str and i in by_id for i in hard_hold_benchmark_ids) and
            len(set(hard_hold_benchmark_ids)) == expected_hard_hold_count, "adoption_hard_hold_ids_invalid")
    hard_hold = set(hard_hold_benchmark_ids)
    require(type(decisions) is list and len(decisions) == expected_count, "adoption_review_coverage_invalid")
    require(all(type(row) is dict and type(row.get("doc_id")) is str for row in decisions), "adoption_review_ids_invalid")
    require(len({row["doc_id"] for row in decisions}) == expected_count and
            {row["doc_id"] for row in decisions} == set(by_id), "adoption_review_ids_invalid")
    for row in decisions:
        key = row["doc_id"]
        _validate_review(row, by_id[key], answer_map[key], evidence_map[key], hard_hold)
    rows = sorted(copy.deepcopy(decisions), key=lambda r: r["doc_id"])
    counts = Counter(row["conditional_reference_decision"] for row in rows)
    benchmark_counts = Counter(row["benchmark_decision"] for row in rows)
    accepted = [r["doc_id"] for r in rows if r["conditional_reference_decision"] == "accept"]
    result = {**FLAGS, "schema_version": LEDGER_SCHEMA, "status": "explicit_ai_internal_reference_decisions_not_training_adoption",
        "source_manifest_sha256": source_manifest_sha256, "policy_sha256": POLICY_SHA256,
        "reviewed_documents": len(rows), "internal_reference_accepted": counts["accept"],
        "internal_reference_held": counts["hold"], "internal_reference_rejected": counts["reject"],
        "benchmark_eligible_candidates": benchmark_counts["eligible"], "benchmark_held_candidates": benchmark_counts["hold"],
        "benchmark_training_adopted": 0, "benchmark_evaluation_adopted": 0,
        "internal_reference_accepted_ids": accepted,
        "internal_reference_held_ids": [r["doc_id"] for r in rows if r["conditional_reference_decision"] == "hold"],
        "internal_reference_rejected_ids": [r["doc_id"] for r in rows if r["conditional_reference_decision"] == "reject"],
        "benchmark_eligible_candidate_ids": [r["doc_id"] for r in rows if r["benchmark_decision"] == "eligible"],
        "accepted_reference_grade_counts": {g: sum(r["reference_grade"] == g and
            r["conditional_reference_decision"] == "accept" for r in rows) for g in GRADES},
        "hard_hold_benchmark_ids": sorted(hard_hold), "source_files_sha256": source_hashes(),
        "documents_sha256": value_digest([d.model_dump() for d in sorted(docs, key=lambda d: d.input.doc_id)]),
        "answers_sha256": value_digest(sorted(answers, key=lambda r: r["doc_id"])),
        "evidence_sha256": value_digest(sorted(evidence, key=lambda r: r["doc_id"])),
        "review_decisions_sha256": value_digest(rows), "decisions": rows,
        "review_authority": "internal_ai_source_review_under_fixed_synthetic_policy",
        "review_reading_claim_independently_verified": False, "semantic_review_truth_certified": False,
        "real_world_premises_verified": False, "customer_gold_created": False,
        "source_document_flags_changed": False, "model_forward_executed": False, "training_performed": False,
        "limitations": ["Literal quote and hash validation do not establish semantic truth or prove actual reading.",
            "Existing conditional labels alone never generate an accept; complete supplied review decisions are required.",
            "Findings are unresolved defects; positive observations belong in body/context review notes.",
            "Benchmark eligible means an individual candidate only, not dataset adoption, release, blindness or accuracy."]}
    result["ledger_sha256"] = value_digest(result)
    return result


def verify_adoption_ledger(ledger, records, answers, evidence, decisions, **kwargs):
    expected = build_adoption_ledger(records, answers, evidence, decisions, **kwargs)
    require(type(ledger) is dict and value_digest(ledger) == value_digest(expected), "adoption_ledger_replay_mismatch")
    return expected
