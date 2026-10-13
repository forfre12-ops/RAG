"""Fictional contract tests only. Submitted identities below are not human signoff."""
from __future__ import annotations

import copy
import json

import pytest

import check_document_review as cli
from koipa.policy_facts import FactContractError, FactPacket, text_digest, value_digest
from koipa.policy_shadow import parse_shadow_policy, policy_digest
from koipa.review_v2 import (
    SAFE_FLAGS, ReviewManifest, blank_submissions, inspect_manifest, manifest_digest, validate_submissions,
)


@pytest.fixture
def setup():
    return cli.demo_inputs()[0]


def bind(manifest, context):
    return context.model_copy(update={"manifest_sha256": manifest_digest(manifest)})


def rehash(manifest):
    manifest["policy_sha256"] = policy_digest(parse_shadow_policy(manifest["policy"]))
    for case in manifest["cases"]:
        case["packet"]["policy_sha256"] = manifest["policy_sha256"]
        case["packet"]["policy_version"] = manifest["policy"]["policy_version"]
        case["packet"] = FactPacket.model_validate(case["packet"]).model_dump()
        case["packet_sha256"] = value_digest(case["packet"])


def worksheet(manifest, context):
    return blank_submissions(manifest, context=context, slot=manifest["assignments"][0]["slot"])


def submitted(manifest, context):
    batch = worksheet(manifest, context)
    for row, case in zip(batch["rows"], manifest["cases"], strict=True):
        row.update(state="submitted", reviewer_ref="fictional-test-actor-not-signoff", actor_kind="human_declared",
                   submitted_at="2026-09-15T02:30:00Z", prior_answers_seen=False,
                   decision={"status": "candidate", "grade": manifest["policy"]["rules"][0]["grade"],
                             "rule_ids": ["DECLARED"], "evidence": [{"fact": "access_scope",
                                 "reference": copy.deepcopy(case["packet"]["facts"][0]["evidence"][0])}],
                             "rationale": "fictional rationale", "not_higher_reason": "fictional upper exclusion",
                             "not_lower_reason": "fictional lower exclusion", "requests": []})
    return batch


def permit(manifest):
    manifest["authorization"].update(status="provided", reference="fictional-permit-not-authenticated", sha256="1" * 64,
        purposes=[manifest["purpose"]], case_ids=[c["case_id"] for c in manifest["cases"]],
        valid_from="2026-09-15T00:00:00Z", valid_until="2026-09-16T00:00:00Z")


def add_case(manifest):
    case = copy.deepcopy(manifest["cases"][0])
    case["case_id"] = "second-case"
    case["document"]["document_id"] = "second-document"
    case["packet"]["document_id"] = "second-document"
    for source in case["packet"]["sources"]:
        source["document_id"] = "second-document"
    manifest["cases"].append(case)
    manifest["assignments"][0]["case_ids"].append(case["case_id"])
    rehash(manifest)
    return case


def test_two_policies_are_injected_not_hardcoded():
    for manifest, context in cli.demo_inputs():
        result = validate_submissions(manifest, submitted(manifest, context), context=context)
        assert result["counts"] == {"assigned": 1, "submitted": 1, "pending": 0, "candidate": 1, "hold": 0, "with_findings": 0}
        assert result["status"] == "submissions_checked_review_required"
        assert all(result[k] == v for k, v in SAFE_FLAGS.items())
        assert result["quality_accuracy"] is None


def test_no_mutation_no_answers_in_templates_or_reports(setup):
    manifest, context = setup
    batch = submitted(manifest, context)
    original = copy.deepcopy((manifest, batch))
    result = validate_submissions(manifest, batch, context=context)
    assert result == validate_submissions(manifest, batch, context=context)
    rendered = json.dumps(result)
    for secret in ("fictional-test-actor", "fictional rationale", "approved_only", '"grade": "S1"'):
        assert secret not in rendered
    template = worksheet(manifest, context)
    assert template["rows"][0]["decision"] is None
    assert template["rows"][0]["reviewer_ref"] is None
    assert "rule_trace" not in json.dumps(template) and "approved_only" not in json.dumps(template)
    result["cases"][0]["findings"].append("output-change")
    assert (manifest, batch) == original
    assert "output-change" not in json.dumps(inspect_manifest(manifest, context=context))


@pytest.mark.parametrize("field,value", [("job_id", "wrong"), ("org_id", "wrong"), ("manifest_sha256", "0" * 64)])
def test_independent_context_binding(setup, field, value):
    manifest, context = setup
    with pytest.raises(FactContractError, match="binding_mismatch"):
        inspect_manifest(manifest, context=context.model_copy(update={field: value}))


@pytest.mark.parametrize("field", ["purpose", "policy_id", "created_at"])
def test_manifest_edits_invalidate_old_context(setup, field):
    manifest, context = setup
    manifest[field] = {"purpose": "evidence_review", "policy_id": "changed", "created_at": "2026-09-15T02:01:00Z"}[field]
    with pytest.raises(FactContractError, match="binding_mismatch"):
        inspect_manifest(manifest, context=context)


@pytest.mark.parametrize("target", ["manifest", "case", "assignment", "packet"])
def test_extra_fields_rejected(setup, target):
    manifest, context = setup
    targets = {"manifest": manifest, "case": manifest["cases"][0], "assignment": manifest["assignments"][0],
               "packet": manifest["cases"][0]["packet"]}
    targets[target]["expected_grade"] = "TS"
    with pytest.raises(FactContractError, match="invalid_review_contract"):
        inspect_manifest(manifest, context=context)


@pytest.mark.parametrize("field,value,code", [
    ("org_id", "other", "document_org_mismatch"), ("document_sha256", "0" * 64, "mismatch"),
    ("as_of", "2026-09-15T04:00:00Z", "evidence_after_manifest"), ("as_of", "2026-09-15", "invalid_timestamp"),
])
def test_document_binding(setup, field, value, code):
    manifest, context = setup
    manifest["cases"][0]["document"][field] = value
    with pytest.raises(FactContractError, match=code):
        inspect_manifest(manifest, context=bind(manifest, context))


@pytest.mark.parametrize("field", ["policy_sha256", "packet_sha256"])
def test_nested_hash_mismatch(setup, field):
    manifest, context = setup
    (manifest if field == "policy_sha256" else manifest["cases"][0])[field] = "0" * 64
    with pytest.raises(FactContractError, match="hash_mismatch"):
        inspect_manifest(manifest, context=bind(manifest, context))


@pytest.mark.parametrize("mutation,code", [
    ("sources", "unpresented_source"), ("duplicate_source", "duplicate_presented_source"),
    ("body", "body_only_contains_context"), ("role", "material_role_mismatch"), ("estimates", "estimates_not_allowed"),
])
def test_input_view_is_actual_material_not_a_declaration(setup, mutation, code):
    manifest, context = setup
    case = manifest["cases"][0]
    if mutation == "sources":
        case["presented_source_ids"] = []
    elif mutation == "duplicate_source":
        case["presented_source_ids"] *= 2
    elif mutation == "body":
        case["input_view"] = "body_only"
    elif mutation == "role":
        case["source_origin"] = "customer_real"
    else:
        case["packet"]["estimates"] = [{"fact": "access_scope", "value": "approved_only",
                                        "origin": "model_estimated", "producer_ref": "fictional"}]
        rehash(manifest)
    with pytest.raises(FactContractError, match=code):
        inspect_manifest(manifest, context=bind(manifest, context))


@pytest.mark.parametrize("status", ["missing_evidence", "policy_gap"])
def test_no_fabricated_evidence_required_for_hold(setup, status):
    manifest, context = setup
    batch = submitted(manifest, context)
    case = manifest["cases"][0]
    case["packet"]["sources"] = []
    case["packet"]["facts"] = []
    case["presented_source_ids"] = []
    case["input_view"] = "body_only"
    rehash(manifest)
    context = bind(manifest, context)
    row = batch["rows"][0]
    row["decision"].update(status=status, grade=None, rule_ids=[], evidence=[], requests=["Obtain missing evidence"])
    empty = worksheet(manifest, context)
    row.update({k: empty["rows"][0][k] for k in ("case_sha256", "packet_sha256", "policy_sha256")})
    batch["manifest_sha256"] = context.manifest_sha256
    result = validate_submissions(manifest, batch, context=context)
    assert result["counts"]["hold"] == 1
    assert status + "_claim_requires_review" in result["rows"][0]["findings"]


@pytest.mark.parametrize("field,value,code", [
    ("reference", "claim", "reference_claim_inconsistent"), ("status", "provided", "reference_claim_inconsistent"),
])
def test_receipt_claim_requires_matching_reference_and_hash(setup, field, value, code):
    manifest, context = setup
    manifest["policy_approval"][field] = value
    with pytest.raises(FactContractError, match=code):
        inspect_manifest(manifest, context=bind(manifest, context))


def test_provided_claims_do_not_grant_authority(setup):
    manifest, context = setup
    permit(manifest)
    for field in ("org_receipt", "policy_approval"):
        manifest[field] = {"status": "provided", "reference": "fictional-claim", "sha256": "2" * 64}
    result = inspect_manifest(manifest, context=bind(manifest, context))
    assert result["status"] == "bound_draft_only" and result["missing_claims"] == []
    assert all(result[k] == v for k, v in SAFE_FLAGS.items())


@pytest.mark.parametrize("field,value,code", [
    ("org_id", "other", "permit_org_mismatch"), ("purposes", ["evidence_review"], "permit_scope_mismatch"),
    ("case_ids", [], "permit_scope_mismatch"), ("valid_until", "2026-09-15T03:00:00Z", "permit_time_invalid"),
    ("valid_from", "2026-09-15T02:01:00Z", "permit_time_invalid"), ("sha256", None, "permit_incomplete"),
    ("status", "unknown", "permit_status_inconsistent"),
])
def test_permit_scope_and_time(setup, field, value, code):
    manifest, context = setup
    permit(manifest)
    manifest["authorization"][field] = value
    with pytest.raises(FactContractError, match=code):
        inspect_manifest(manifest, context=bind(manifest, context))


@pytest.mark.parametrize("origin", ["customer_real", "public_real"])
def test_real_declared_material_needs_permit_for_blank_export(setup, origin):
    manifest, context = setup
    manifest["cases"][0]["source_origin"] = origin
    manifest["cases"][0]["packet"]["material_role"] = "unverified_supplied_assertions"
    rehash(manifest)
    context = bind(manifest, context)
    assert inspect_manifest(manifest, context=context)["status"] == "needs_input_claims"
    with pytest.raises(FactContractError, match="permission_claim_required"):
        worksheet(manifest, context)
    permit(manifest)
    assert worksheet(manifest, bind(manifest, context))["rows"][0]["decision"] is None


def test_explicit_denial_blocks_even_synthetic_export(setup):
    manifest, context = setup
    manifest["authorization"]["status"] = "denied"
    with pytest.raises(FactContractError, match="permission_denied"):
        worksheet(manifest, bind(manifest, context))


@pytest.mark.parametrize("mode", ["family", "body", "original"])
def test_partitions_do_not_split_same_family_or_hash(setup, mode):
    manifest, context = setup
    second = add_case(manifest)
    second["partition"] = "sealed_candidate"
    if mode != "family":
        second["family_id"] = "new-family"
    if mode == "original":
        second["document"]["document_sha256"] = text_digest("another-text")
    with pytest.raises(FactContractError, match=mode + "_partition_overlap"):
        inspect_manifest(manifest, context=bind(manifest, context))


@pytest.mark.parametrize("kind", ["case", "document", "slot", "assignment_case", "unknown_case"])
def test_duplicate_or_unknown_ids_rejected(setup, kind):
    manifest, context = setup
    if kind in {"case", "document"}:
        manifest["cases"].append(copy.deepcopy(manifest["cases"][0]))
        if kind == "document":
            manifest["cases"][1]["case_id"] = "other"
    elif kind == "slot":
        manifest["assignments"] *= 2
    else:
        manifest["assignments"][0]["case_ids"].append("fictional-case" if kind == "assignment_case" else "missing")
    with pytest.raises(FactContractError, match="duplicate|unknown_assignment"):
        inspect_manifest(manifest, context=bind(manifest, context))


@pytest.mark.parametrize("rows", [[], "duplicate", "other", "missing"])
def test_submission_coverage_exact(setup, rows):
    manifest, context = setup
    add_case(manifest)
    context = bind(manifest, context)
    batch = worksheet(manifest, context)
    if rows == "duplicate":
        batch["rows"].append(copy.deepcopy(batch["rows"][0]))
    elif rows == "other":
        batch["rows"][0]["case_id"] = "other"
    elif rows == "missing":
        batch["rows"].pop()
    else:
        batch["rows"] = rows
    with pytest.raises(FactContractError):
        validate_submissions(manifest, batch, context=context)


@pytest.mark.parametrize("field", ["manifest_sha256", "case_sha256", "packet_sha256", "policy_sha256"])
def test_submission_hash_binding(setup, field):
    manifest, context = setup
    batch = submitted(manifest, context)
    (batch if field == "manifest_sha256" else batch["rows"][0])[field] = "0" * 64
    with pytest.raises(FactContractError, match="binding_mismatch|revision_mismatch"):
        validate_submissions(manifest, batch, context=context)


@pytest.mark.parametrize("field", ["reviewer_ref", "actor_kind", "submitted_at", "prior_answers_seen", "decision"])
def test_pending_cannot_carry_submission_fields(setup, field):
    manifest, context = setup
    batch = worksheet(manifest, context)
    batch["rows"][0][field] = submitted(manifest, context)["rows"][0][field]
    with pytest.raises(FactContractError, match="pending_contains_answer"):
        validate_submissions(manifest, batch, context=context)


@pytest.mark.parametrize("field", ["reviewer_ref", "actor_kind", "submitted_at", "prior_answers_seen", "decision"])
def test_submitted_requires_all_provenance_fields(setup, field):
    manifest, context = setup
    batch = submitted(manifest, context)
    batch["rows"][0][field] = None
    with pytest.raises(FactContractError, match="submission_incomplete"):
        validate_submissions(manifest, batch, context=context)


@pytest.mark.parametrize("at", ["2026-09-15T01:59:59Z", "2026-09-15T03:00:01Z", "2026-09-15T02:30:00"])
def test_submission_time(setup, at):
    manifest, context = setup
    batch = submitted(manifest, context)
    batch["rows"][0]["submitted_at"] = at
    with pytest.raises(FactContractError, match="time"):
        validate_submissions(manifest, batch, context=context)


@pytest.mark.parametrize("field,value,code", [
    ("rule_ids", ["CP-HOLD"], "unknown_rule"), ("rule_ids", ["DECLARED", "DECLARED"], "duplicate_cited_rule"),
    ("grade", "TS", "rule_grade_mismatch"), ("grade", None, "candidate_incomplete"),
    ("evidence", [], "candidate_incomplete"), ("requests", ["unresolved"], "candidate_incomplete"),
    ("rationale", " ", "invalid_review_contract"),
])
def test_invalid_candidate_structure(setup, field, value, code):
    manifest, context = setup
    batch = submitted(manifest, context)
    batch["rows"][0]["decision"][field] = value
    with pytest.raises(FactContractError, match=code):
        validate_submissions(manifest, batch, context=context)


@pytest.mark.parametrize("field,value,code", [
    ("source_id", "hidden", "source_not_presented"), ("source_sha256", "0" * 64, "source_hash_mismatch"),
    ("value_sha256", "0" * 64, "value_hash_mismatch"),
    ("locator", {"kind": "json_pointer", "pointer": "/missing"}, "pointer"),
    ("locator", {"kind": "text_span", "start": 0, "end": 2}, "locator_type_mismatch"),
])
def test_citation_binding(setup, field, value, code):
    manifest, context = setup
    batch = submitted(manifest, context)
    batch["rows"][0]["decision"]["evidence"][0]["reference"][field] = value
    with pytest.raises(FactContractError, match=code):
        validate_submissions(manifest, batch, context=context)


@pytest.mark.parametrize("fact", ["unknown_fact", "public_disclosed"])
def test_citations_cannot_relabel_facts(setup, fact):
    manifest, context = setup
    batch = submitted(manifest, context)
    batch["rows"][0]["decision"]["evidence"][0]["fact"] = fact
    with pytest.raises(FactContractError, match="citation_fact|not_bound_to_fact"):
        validate_submissions(manifest, batch, context=context)


def test_policy_disagreement_retained_not_rewritten(setup):
    manifest, context = setup
    batch = submitted(manifest, context)
    batch["rows"][0]["decision"].update(rule_ids=["OTHER"], grade="S3")
    before = copy.deepcopy(batch)
    result = validate_submissions(manifest, batch, context=context)
    assert batch == before and batch["rows"][0]["decision"]["grade"] == "S3"
    assert result["rows"][0]["findings"] == ["cited_rule_not_supported_by_supplied_facts",
                                             "submitted_grade_differs_from_policy_candidate"]
    assert result["gold_eligible"] is False


def test_higher_unknown_rule_is_not_a_lower_grade_answer_key(setup):
    manifest, context = setup
    manifest["policy"]["rules"].append({"id": "HIGH", "grade": "TS", "priority": 1,
                                         "when": {"public_disclosed": {"value": False}}})
    rehash(manifest)
    context = bind(manifest, context)
    result = validate_submissions(manifest, submitted(manifest, context), context=context)
    assert "policy_blocks_candidate" in result["rows"][0]["findings"]
    assert result["final_grade"] is None


@pytest.mark.parametrize("state", ["unknown", "incomplete"])
def test_extraction_findings_not_silently_discarded(setup, state):
    manifest, context = setup
    manifest["cases"][0]["extraction_state"] = state
    context = bind(manifest, context)
    result = validate_submissions(manifest, submitted(manifest, context), context=context)
    assert "extraction_" + state in result["rows"][0]["findings"]


def test_identity_and_exposure_are_claims_only(setup):
    manifest, context = setup
    batch = submitted(manifest, context)
    batch["rows"][0].update(prior_answers_seen=True, actor_kind="ai_assisted_declared")
    result = validate_submissions(manifest, batch, context=context)
    assert result["rows"][0]["findings"] == ["prior_answer_exposure_declared", "ai_assistance_declared"]
    assert result["identity_authenticated"] is False and result["independence_verified"] is False


def test_worksheet_coverage_and_counts_allow_pending(setup):
    manifest, context = setup
    add_case(manifest)
    context = bind(manifest, context)
    batch = submitted(manifest, context)
    batch["rows"][1] = worksheet(manifest, context)["rows"][1]
    result = validate_submissions(manifest, batch, context=context)
    assert result["status"] == "awaiting_submissions" and result["counts"]["pending"] == 1
    assert result["quality_accuracy"] is None


def test_slot_multiple_claimed_people_rejected(setup):
    manifest, context = setup
    add_case(manifest)
    context = bind(manifest, context)
    batch = submitted(manifest, context)
    batch["rows"][1]["reviewer_ref"] = "another-fictional-actor"
    with pytest.raises(FactContractError, match="multiple_identities"):
        validate_submissions(manifest, batch, context=context)


def write_inputs(tmp_path, setup):
    manifest, context = setup
    paths = [tmp_path / "manifest.json", tmp_path / "context.json"]
    for path, data in zip(paths, (manifest, context.model_dump()), strict=True):
        path.write_text(json.dumps(data), encoding="utf-8")
    return ["--manifest", str(paths[0]), "--context", str(paths[1])]


def test_cli_end_to_end_blank_then_validate(tmp_path, setup, capsys):
    args = write_inputs(tmp_path, setup)
    template, report = tmp_path / "blank.json", tmp_path / "report.json"
    assert cli.main([*args, "--slot", "independent-A", "--template-out", str(template), "--out", str(report)]) == 3
    assert json.loads(template.read_text())["rows"][0]["decision"] is None
    assert "implementation_sha256" in json.loads(report.read_text())
    assert cli.main([*args, "--submissions", str(template)]) == 3
    assert "awaiting_submissions" in capsys.readouterr().out
    before = template.read_bytes()
    assert cli.main([*args, "--slot", "independent-A", "--template-out", str(template)]) == 2
    assert template.read_bytes() == before


@pytest.mark.parametrize("args", [[], ["--demo", "--schema"], ["--demo", "--slot", "A"], ["--slot", "A"],
                                 ["--template-out", "unused.json"], ["--schema", "--context", "absent.json"]])
def test_cli_invalid_modes(args, capsys):
    assert cli.main(args) == 2
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "INVALID_REVIEW_INPUT" and result["gold_eligible"] is False


@pytest.mark.parametrize("bad", ['{"secret": "DO_NOT_PRINT", "secret": 1}', '{"secret": NaN}', '{malformed'])
def test_cli_strict_json_and_no_payload_on_error(tmp_path, setup, capsys, bad):
    args = write_inputs(tmp_path, setup)
    (tmp_path / "manifest.json").write_text(bad, encoding="utf-8")
    assert cli.main(args) == 2
    assert "DO_NOT_PRINT" not in capsys.readouterr().out


def test_cli_input_change_detected_before_output(tmp_path, setup, monkeypatch, capsys):
    args = write_inputs(tmp_path, setup)
    original = cli.inspect_manifest
    def change(raw, *, context):
        result = original(raw, context=context)
        (tmp_path / "manifest.json").write_text("{}", encoding="utf-8")
        return result
    monkeypatch.setattr(cli, "inspect_manifest", change)
    output = tmp_path / "report.json"
    assert cli.main([*args, "--out", str(output)]) == 2
    assert not output.exists() and "input_changed_during_check" in capsys.readouterr().out


def test_cli_outputs_must_not_overlap_inputs(tmp_path, setup):
    args = write_inputs(tmp_path, setup)
    assert cli.main([*args, "--out", args[1]]) == 2
    assert cli.main([*args, "--slot", "independent-A", "--template-out", str(tmp_path / "same.json"),
                     "--out", str(tmp_path / "same.json")]) == 2


def test_cli_demo_and_schema(tmp_path, capsys):
    assert cli.main(["--demo"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["real_documents"] == 0 and result["human_submissions"] == 0
    assert len(result["results"]) == 2
    path = tmp_path / "schema.json"
    assert cli.main(["--schema", "--out", str(path)]) == 0
    assert json.loads(path.read_text())["cross_field_checks_required"] is True


def test_constructed_model_is_revalidated(setup):
    manifest, context = setup
    invalid = ReviewManifest.model_construct(**{**manifest, "cases": []})
    with pytest.raises(FactContractError, match="invalid_review_contract"):
        inspect_manifest(invalid, context=context)


@pytest.mark.parametrize("field,value,code", [
    ("assigned_at", "2026-09-15T01:00:00Z", "assignment_time_invalid"),
    ("assigned_at", "2026-09-15T04:00:00Z", "assignment_time_invalid"),
    ("identity_mode", "self_declared", "assignment_identity_inconsistent"),
    ("reviewer_ref", "fictional-actor", "assignment_identity_inconsistent"),
])
def test_assignment_contract(setup, field, value, code):
    manifest, context = setup
    manifest["assignments"][0][field] = value
    with pytest.raises(FactContractError, match=code):
        inspect_manifest(manifest, context=bind(manifest, context))


def test_assigned_identity_checked_but_not_authenticated(setup):
    manifest, context = setup
    manifest["assignments"][0].update(identity_mode="upstream_claimed", reviewer_ref="fictional-test-actor-not-signoff")
    context = bind(manifest, context)
    batch = submitted(manifest, context)
    assert validate_submissions(manifest, batch, context=context)["identity_authenticated"] is False
    batch["rows"][0]["reviewer_ref"] = "someone-else"
    with pytest.raises(FactContractError, match="assigned_identity_mismatch"):
        validate_submissions(manifest, batch, context=context)


@pytest.mark.parametrize("field,value", [("job_id", "other"), ("org_id", "other"), ("slot", "other")])
def test_submission_other_job_org_slot_rejected(setup, field, value):
    manifest, context = setup
    batch = worksheet(manifest, context)
    batch[field] = value
    with pytest.raises(FactContractError, match="binding_mismatch|unknown_slot"):
        validate_submissions(manifest, batch, context=context)


def test_policy_conflict_is_a_review_claim_not_machine_truth(setup):
    manifest, context = setup
    batch = submitted(manifest, context)
    decision = batch["rows"][0]["decision"]
    decision.update(status="policy_conflict", grade=None, rule_ids=["DECLARED", "OTHER"],
                    requests=["Resolve interpretation"], evidence=[])
    result = validate_submissions(manifest, batch, context=context)
    assert result["rows"][0]["findings"] == ["policy_conflict_claim_requires_review"]
    decision["rule_ids"] = ["DECLARED"]
    with pytest.raises(FactContractError, match="conflict_requires_rules"):
        validate_submissions(manifest, batch, context=context)


@pytest.mark.parametrize("field,value", [("grade", "S1"), ("requests", [])])
def test_hold_requires_no_grade_and_a_request(setup, field, value):
    manifest, context = setup
    batch = submitted(manifest, context)
    decision = batch["rows"][0]["decision"]
    decision.update(status="missing_evidence", grade=None, rule_ids=[], evidence=[], requests=["Obtain evidence"])
    decision[field] = value
    with pytest.raises(FactContractError, match="hold_incomplete"):
        validate_submissions(manifest, batch, context=context)


def text_case(manifest):
    case = manifest["cases"][0]
    text = "가상 문서 😀 실행 수치"
    sha = text_digest(text)
    case["document"]["document_sha256"] = sha
    case["input_view"] = "body_only"
    case["packet"].update(document_sha256=sha, facts=[], sources=[{
        "source_id": "body", "kind": "document_text", "org_id": manifest["org_id"],
        "document_id": case["document"]["document_id"], "document_sha256": sha,
        "payload": text, "payload_sha256": sha, "captured_at": "2026-09-15T00:00:00Z", "source_ref": "fictional-body"}])
    case["presented_source_ids"] = ["body"]
    rehash(manifest)
    return {"reference": {"source_id": "body", "source_sha256": sha,
                          "locator": {"kind": "text_span", "start": 0, "end": len(text)}, "value_sha256": sha}, "fact": None}


@pytest.mark.parametrize("variant", ["valid", "beyond", "empty", "wrong_hash", "wrong_kind", "duplicate"])
def test_text_citation_character_offsets_and_hashes(setup, variant):
    manifest, context = setup
    batch = submitted(manifest, context)
    citation = text_case(manifest)
    context = bind(manifest, context)
    fresh = worksheet(manifest, context)
    for key in ("case_sha256", "packet_sha256", "policy_sha256"):
        batch["rows"][0][key] = fresh["rows"][0][key]
    batch["manifest_sha256"] = context.manifest_sha256
    decision = batch["rows"][0]["decision"]
    decision.update(status="missing_evidence", grade=None, rule_ids=[], evidence=[citation], requests=["Metadata needed"])
    ref = citation["reference"]
    if variant == "beyond":
        ref["locator"]["end"] = 999
    elif variant == "empty":
        ref["locator"].update(start=1, end=1)
    elif variant == "wrong_hash":
        ref["value_sha256"] = "0" * 64
    elif variant == "wrong_kind":
        ref["locator"] = {"kind": "json_pointer", "pointer": "/text"}
    elif variant == "duplicate":
        decision["evidence"].append(copy.deepcopy(citation))
    if variant == "valid":
        assert validate_submissions(manifest, batch, context=context)["counts"]["hold"] == 1
        manifest["cases"][0]["input_view"] = "evidence_only"
        with pytest.raises(FactContractError, match="evidence_only_contains_body"):
            inspect_manifest(manifest, context=bind(manifest, context))
    else:
        with pytest.raises(FactContractError, match="citation"):
            validate_submissions(manifest, batch, context=context)


@pytest.mark.parametrize("value", [None, False, "", [], 0, {"x": 1}])
def test_json_citations_preserve_exact_null_false_empty_values(setup, value):
    manifest, context = setup
    source = manifest["cases"][0]["packet"]["sources"][0]
    source["payload"]["extra"] = value
    source["payload_sha256"] = value_digest(source["payload"])
    manifest["cases"][0]["packet"]["facts"][0]["evidence"][0]["source_sha256"] = source["payload_sha256"]
    rehash(manifest)
    context = bind(manifest, context)
    batch = submitted(manifest, context)
    citation = batch["rows"][0]["decision"]["evidence"][0]
    citation["fact"] = None
    citation["reference"].update(locator={"kind": "json_pointer", "pointer": "/extra"}, value_sha256=value_digest(value))
    result = validate_submissions(manifest, batch, context=context)
    assert result["rows"][0]["findings"] == ["required_rule_facts_not_cited"]


def test_old_submission_rejected_after_rebound_manifest_change(setup):
    manifest, context = setup
    batch = submitted(manifest, context)
    manifest["policy"]["policy_version"] = "new-version"
    rehash(manifest)
    with pytest.raises(FactContractError, match="binding_mismatch"):
        validate_submissions(manifest, batch, context=bind(manifest, context))


@pytest.mark.parametrize("field,value", [("prior_answers_seen", 0), ("prior_answers_seen", "false"), ("state", "approved")])
def test_submission_no_coercion_or_approval_state(setup, field, value):
    manifest, context = setup
    batch = submitted(manifest, context)
    batch["rows"][0][field] = value
    with pytest.raises(FactContractError, match="invalid_review_contract"):
        validate_submissions(manifest, batch, context=context)


def test_zero_cases_cannot_report_success(setup):
    manifest, context = setup
    manifest["cases"] = []
    with pytest.raises(FactContractError, match="invalid_review_contract"):
        inspect_manifest(manifest, context=context)
