"""Metadata fixtures only; these do not represent actual customer documents."""
from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest

POC = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(POC / "scripts"))

import classification_pilot_intake as intake


def sample():
    row = intake.template()
    row.update(intake_id="TEST-ONLY-1", tenant_ref="fixture-tenant", document_ref="fixture-vault-ref",
               document_version="test-revision", source_origin="customer_real", as_of="2026-09-14T00:00:00+00:00",
               original_sha256="a" * 64, text_sha256="b" * 64, normalized_text_sha256="c" * 64,
               family_id="fixture-family", family_evidence_ref="fixture-family-record")
    row["authorization"].update(status="provided", reference="fixture-permission", sha256="d" * 64,
                                tenant_ref="fixture-tenant", allowed_use=["local_review"])
    row["policy"].update(version="test-policy", sha256="e" * 64)
    row["extraction"].update(complete_claimed=True, source_pages=4, extracted_pages=4,
                             expected_attachments=1, included_attachments=1, verification_ref="fixture-extraction-record",
                             verification_sha256="f" * 64)
    row["context"].update(state="provided", reference="fixture-context", sha256="1" * 64, provided_to_reviewer=True)
    return row


def complete_index():
    index = intake.exclusion_template()
    index.update(coverage_complete=True, coverage_reference="fixture-inventory", coverage_sha256="2" * 64)
    return index


def entry(row, role, field="family_id"):
    return {"document_ref": None, "original_sha256": None, "text_sha256": None,
            "normalized_text_sha256": None, "family_id": None, "role": role} | {field: row[field]}


def test_empty_intake_is_waiting_not_missing_documents_invented():
    result = intake.preflight([], intake.exclusion_template())
    assert result["input_count"] == result["selected_count"] == result["original_documents_read"] == 0
    assert result["state"] == "awaiting_real_document_intake"
    assert result["index_coverage_claimed"] is False


def test_complete_claims_only_allow_source_verification_candidate():
    result = intake.preflight([sample()], complete_index())
    row = result["rows"][0]
    assert row["status"] == "candidate_pending_source_verification"
    assert row["selected"] is row["permission_authenticated"] is row["source_verified"] is row["training_allowed"] is False
    assert not result["source_authenticity_verified"] and not result["index_coverage_authenticated"]


@pytest.mark.parametrize("field", ["tenant_ref", "document_ref", "document_version", "family_id", "family_evidence_ref",
                                   "original_sha256", "text_sha256", "normalized_text_sha256", "as_of"])
def test_missing_metadata_is_not_filled_with_guesses(field):
    row = sample()
    row[field] = None
    before = copy.deepcopy(row)
    result = intake.preflight([row], complete_index())
    assert result["rows"][0]["status"] == "needs_metadata"
    assert row == before


@pytest.mark.parametrize("change", ["permission", "tenant_mismatch", "use_scope", "policy", "naive_date", "missing_pages",
                                     "attachments", "count_bool", "extraction_evidence", "context", "context_hidden",
                                     "not_required_unproven", "index", "customer_policy"])
def test_incomplete_scope_and_authority_are_not_ready(change):
    row, index = sample(), complete_index()
    if change == "permission":
        row["authorization"]["reference"] = None
    elif change == "tenant_mismatch":
        row["authorization"]["tenant_ref"] = "another"
    elif change == "use_scope":
        row["authorization"]["allowed_use"] = ["index_only"]
    elif change == "policy":
        row["policy"]["sha256"] = "invalid"
    elif change == "naive_date":
        row["as_of"] = "2026-09-14T00:00:00"
    elif change == "missing_pages":
        row["extraction"]["extracted_pages"] = 3
    elif change == "attachments":
        row["extraction"]["included_attachments"] = 0
    elif change == "count_bool":
        row["extraction"]["source_pages"] = True
        row["extraction"]["extracted_pages"] = True
    elif change == "extraction_evidence":
        row["extraction"]["verification_ref"] = None
    elif change == "context":
        row["context"]["state"] = "unknown"
    elif change == "context_hidden":
        row["context"]["provided_to_reviewer"] = False
    elif change == "not_required_unproven":
        row["context"].update(state="not_required", reference=None)
    elif change == "index":
        index = intake.exclusion_template()
    else:
        row["policy"]["scope"] = "customer_management"
    result = intake.preflight([row], index)
    assert result["rows"][0]["status"] == "needs_metadata"
    assert result["selected_count"] == 0


@pytest.mark.parametrize("reason", ["synthetic", "synthetic_context", "denied", "public_not_customer", "protected"])
def test_ineligible_records_are_explicitly_excluded(reason):
    row = sample()
    if reason == "synthetic":
        row["source_origin"] = "synthetic"
    elif reason == "synthetic_context":
        row["context"]["state"] = "synthetic_assumption"
    elif reason == "denied":
        row["authorization"]["status"] = "denied"
    elif reason == "public_not_customer":
        row.update(intended_use="customer_eval_pool", source_origin="public_real")
    else:
        row["source_role"] = "held_review"
    assert intake.preflight([row], complete_index())["rows"][0]["status"] == "excluded"


@pytest.mark.parametrize("field", ["document_ref", "original_sha256", "text_sha256", "normalized_text_sha256", "family_id"])
def test_exclusion_matches_any_identity_not_just_body_hash(field):
    row, index = sample(), complete_index()
    index["entries"] = [entry(row, "locked_gold_eval", field)]
    report = intake.preflight([row], index)["rows"][0]
    assert report["status"] == "excluded"
    assert report["overlap_matches"][0]["matched_fields"] == [field]


def test_train_history_permitted_for_label_only_candidate_but_not_customer_eval():
    row, index = sample(), complete_index()
    row.update(source_role="train", intended_use="label_only_ab_pool")
    index["entries"] = [entry(row, "train")]
    assert intake.preflight([row], index)["rows"][0]["status"] == "candidate_pending_source_verification"
    row["intended_use"] = "customer_eval_pool"
    assert intake.preflight([row], index)["rows"][0]["status"] == "excluded"
    row["intended_use"] = "label_only_ab_pool"
    index["entries"].append(entry(row, "held_review"))
    assert intake.preflight([row], index)["rows"][0]["status"] == "excluded"


def test_batch_duplicates_need_grouping_and_cannot_be_two_independent_documents():
    a, b = sample(), sample()
    b["intake_id"] = "TEST-ONLY-2"
    result = intake.preflight([a, b], complete_index())
    assert all(r["status"] == "needs_metadata" for r in result["rows"])
    assert all("batch_shared_family_id_requires_grouping" in r["missing_requirements"] for r in result["rows"])


def test_raw_body_or_unknown_fields_are_not_accepted():
    row = sample() | {"text": "DO NOT COPY CUSTOMER BODY HERE"}
    with pytest.raises(ValueError, match="raw body"):
        intake.preflight([row], complete_index())


def test_duplicate_intake_ids_rejected():
    with pytest.raises(ValueError, match="duplicate"):
        intake.preflight([sample(), sample()], complete_index())


def test_complete_index_needs_coverage_evidence():
    index = intake.exclusion_template()
    index["coverage_complete"] = True
    with pytest.raises(ValueError, match="Coverage"):
        intake.preflight([], index)


def test_missing_index_identity_and_bad_hash_fail():
    index = complete_index()
    item = entry(sample(), "held_review")
    item["family_id"] = None
    index["entries"] = [item]
    with pytest.raises(ValueError, match="Empty exclusion"):
        intake.preflight([], index)
    item["text_sha256"] = "not a digest"
    with pytest.raises(ValueError, match="hash"):
        intake.preflight([], index)


def test_preflight_does_not_open_referenced_documents(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("Metadata preflight must not open raw documents")
    monkeypatch.setattr(Path, "open", forbidden)
    assert intake.preflight([sample()], complete_index())["original_documents_read"] == 0
