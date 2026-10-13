"""Fictional review pairs/proposals only; none are human signoff or GOLD."""
from __future__ import annotations

import copy
import json

import pytest

import compare_document_reviews as cli
from koipa.policy_facts import FactContractError, FactPacket, value_digest
from koipa.policy_shadow import parse_shadow_policy, policy_digest
from koipa.review_pair_v2 import (
    AdjudicationBatch, blank_adjudications, compare_submissions, validate_adjudications,
)
from koipa.review_v2 import SAFE_FLAGS, blank_submissions, manifest_digest


@pytest.fixture
def setup():
    return cli.demo_inputs()[0]


def fill(setup):
    manifest, _, left, right = setup
    cases = {c["case_id"]: c for c in manifest["cases"]}
    for side, batch in (("left", left), ("right", right)):
        for row in batch["rows"]:
            case = cases[row["case_id"]]
            row.update(state="submitted", reviewer_ref="fictional-not-signoff-" + side, actor_kind="human_declared",
                       submitted_at="2026-09-15T02:30:00Z", prior_answers_seen=False,
                       decision={"status": "candidate", "grade": manifest["policy"]["rules"][0]["grade"],
                                 "rule_ids": ["DECLARED"], "evidence": [{"fact": "access_scope",
                                     "reference": copy.deepcopy(case["packet"]["facts"][0]["evidence"][0])}],
                                 "rationale": "fictional private rationale", "not_higher_reason": "fictional upper",
                                 "not_lower_reason": "fictional lower", "requests": []})
    return setup


def compare(setup):
    m, c, a, b = setup
    return compare_submissions(m, a, b, context=c)


def blank(setup):
    m, c, a, b = setup
    return blank_adjudications(m, a, b, context=c)


def proposal(setup):
    result, comparison = blank(setup), compare(setup)
    decisions = {r["case_id"]: r["decision"] for r in setup[2]["rows"]}
    issues = {r["case_id"]: r["issue_codes"] for r in comparison["rows"]}
    for row in result["rows"]:
        row.update(state="proposed", adjudicator_ref="fictional-third-person-not-signoff", actor_kind="human_declared",
                   proposed_at="2026-09-15T02:45:00Z", reviewed_both=True, cause_codes=["agreement_check"],
                   resolution_note="fictional private resolution", decision=copy.deepcopy(decisions[row["case_id"]]),
                   issue_responses=[{"issue_code": code, "action": "interpretation_proposed", "reason": "fictional reason"}
                                    for code in issues[row["case_id"]]])
    return result


def validate(setup, proposed):
    m, c, a, b = setup
    return validate_adjudications(m, a, b, proposed, context=c)


def refresh(setup):
    m, c, a, b = setup
    m["policy_sha256"] = policy_digest(parse_shadow_policy(m["policy"]))
    for case in m["cases"]:
        case["packet"].update(policy_sha256=m["policy_sha256"], policy_version=m["policy"]["policy_version"])
        case["packet"] = FactPacket.model_validate(case["packet"]).model_dump()
        case["packet_sha256"] = value_digest(case["packet"])
    c = c.model_copy(update={"manifest_sha256": manifest_digest(m)})
    a, b = [blank_submissions(m, context=c, slot=batch["slot"]) for batch in (a, b)]
    return m, c, a, b


def second_case(setup):
    m = setup[0]
    case = copy.deepcopy(m["cases"][0])
    case["case_id"] = "fictional-second"
    case["document"]["document_id"] = "fictional-second-doc"
    case["packet"]["document_id"] = "fictional-second-doc"
    for source in case["packet"]["sources"]:
        source["document_id"] = "fictional-second-doc"
    m["cases"].append(case)
    for assignment in m["assignments"]:
        assignment["case_ids"].append(case["case_id"])
    return refresh(setup)


def hold(row, status="missing_evidence"):
    row["decision"].update(status=status, grade=None, rule_ids=[], evidence=[], requests=["Obtain missing evidence"])


@pytest.mark.parametrize("index", [0, 1])
def test_two_injected_policies_and_agreement_is_not_truth(index):
    setup = fill(cli.demo_inputs()[index])
    result = compare(setup)
    assert result["agreement_all_completed"]["grade_agreement"] == {"numerator": 1, "denominator": 1, "rate": 1.0}
    assert result["counts"]["declared_clean_pairs"] == 1
    assert result["rows"][0]["differences"] == []
    assert all(result[k] == v for k, v in SAFE_FLAGS.items())
    assert result["quality_accuracy"] is None and result["root_causes_verified"] is False
    checked = validate(setup, proposal(setup))
    assert checked["proposed_count"] == 1 and checked["adjudication_performed"] is False
    assert checked["final_grade"] is None and checked["gold_eligible"] is False


def test_pending_pairs_zero_denominators(setup):
    result = compare(setup)
    assert result["status"] == "awaiting_pairs"
    assert result["counts"]["assigned_pairs"] == result["counts"]["pending_pairs"] == 1
    for metrics in (result["agreement_all_completed"], result["agreement_declared_clean_only"]):
        for field in ("grade_agreement", "outcome_agreement", "rule_set_agreement", "evidence_set_agreement"):
            assert metrics[field] == {"numerator": 0, "denominator": 0, "rate": None}
    checked = validate(setup, blank(setup))
    assert checked["pending_proposal_count"] == 1 and checked["proposed_count"] == 0


def test_partial_completion_not_silently_dropped(setup):
    setup = second_case(setup)
    pending = copy.deepcopy(setup[3]["rows"][1])
    fill(setup)
    setup[3]["rows"][1] = pending
    result = compare(setup)
    assert result["counts"]["assigned_pairs"] == 2
    assert result["counts"]["completed_pairs"] == result["counts"]["pending_pairs"] == 1
    assert result["agreement_all_completed"]["grade_agreement"]["denominator"] == 1


@pytest.mark.parametrize("status", ["missing_evidence", "policy_gap"])
def test_matching_hold_not_counted_as_grade_agreement(setup, status):
    fill(setup)
    for batch in setup[2:]:
        hold(batch["rows"][0], status)
    result = compare(setup)
    assert result["agreement_all_completed"]["outcome_agreement"]["rate"] == 1
    assert result["agreement_all_completed"]["grade_agreement"]["rate"] is None
    assert result["rows"][0]["issue_codes"]  # Holds still need review.
    assert validate(setup, proposal(setup))["adjudication_rows"][0]["decision_status"] == status


def test_candidate_vs_hold_denominators_and_differences(setup):
    fill(setup)
    hold(setup[3]["rows"][0])
    result = compare(setup)
    assert result["agreement_all_completed"]["outcome_agreement"] == {"numerator": 0, "denominator": 1, "rate": 0.0}
    assert result["agreement_all_completed"]["grade_agreement"]["denominator"] == 0
    assert {"status", "grade", "rule_ids", "evidence", "requests"} <= set(result["rows"][0]["differences"])


def test_grade_disagreement_and_shared_wrong_answer_are_both_preserved(setup):
    fill(setup)
    setup[3]["rows"][0]["decision"].update(grade="S3", rule_ids=["OTHER"])
    result = compare(setup)
    assert result["agreement_all_completed"]["grade_agreement"]["rate"] == 0
    assert "right:submitted_grade_differs_from_policy_candidate" in result["rows"][0]["issue_codes"]
    setup[2]["rows"][0]["decision"].update(grade="S3", rule_ids=["OTHER"])
    result = compare(setup)
    assert result["agreement_all_completed"]["grade_agreement"]["rate"] == 1
    assert "left:submitted_grade_differs_from_policy_candidate" in result["rows"][0]["issue_codes"]
    checked = validate(setup, proposal(setup))
    assert "submitted_grade_differs_from_policy_candidate" in checked["adjudication_rows"][0]["proposal_findings"]
    assert checked["gold_eligible"] is False


@pytest.mark.parametrize("field,value", [("rationale", "different"), ("not_higher_reason", "different"),
                                       ("not_lower_reason", "different"), ("evidence", "different_fact_annotation")])
def test_non_grade_disagreements_not_label_errors(setup, field, value):
    fill(setup)
    decision = setup[3]["rows"][0]["decision"]
    if field == "evidence":
        decision["evidence"][0]["fact"] = None
    else:
        decision[field] = value
    result = compare(setup)
    assert result["rows"][0]["differences"] == [field]
    assert result["agreement_all_completed"]["grade_agreement"]["rate"] == 1
    assert "label_error" not in json.dumps(result)


def test_rules_and_citations_are_compared_as_sets(setup):
    m = setup[0]
    duplicate = copy.deepcopy(m["policy"]["rules"][0])
    duplicate["id"] = "EQUIVALENT"
    m["policy"]["rules"].append(duplicate)
    setup = fill(refresh(setup))
    for batch in setup[2:]:
        decision = batch["rows"][0]["decision"]
        decision["rule_ids"].append("EQUIVALENT")
        citation = copy.deepcopy(decision["evidence"][0])
        citation["fact"] = None
        decision["evidence"].append(citation)
    setup[3]["rows"][0]["decision"]["rule_ids"].reverse()
    setup[3]["rows"][0]["decision"]["evidence"].reverse()
    assert compare(setup)["rows"][0]["differences"] == []


@pytest.mark.parametrize("issue", ["same_actor", "left_exposed", "right_exposed", "left_ai", "right_ai"])
def test_raw_agreement_and_declared_clean_subset_are_separate(setup, issue):
    fill(setup)
    if issue == "same_actor":
        setup[3]["rows"][0]["reviewer_ref"] = setup[2]["rows"][0]["reviewer_ref"]
    else:
        side, kind = issue.split("_")
        row = setup[2 if side == "left" else 3]["rows"][0]
        row["prior_answers_seen" if kind == "exposed" else "actor_kind"] = True if kind == "exposed" else "ai_assisted_declared"
    result = compare(setup)
    assert result["agreement_all_completed"]["outcome_agreement"]["rate"] == 1
    assert result["agreement_declared_clean_only"]["outcome_agreement"]["rate"] is None
    assert result["rows"][0]["exclusion_reasons"]
    checked = validate(setup, proposal(setup))
    assert checked["counts"]["declared_clean_pairs"] == 0
    assert checked["adjudication_rows"][0]["original_issue_codes"] == result["rows"][0]["issue_codes"]


def test_higher_unknown_and_extraction_gap_remain_after_proposal(setup):
    m = setup[0]
    m["policy"]["rules"].append({"id": "HIGH", "grade": "TS", "priority": 1,
                                  "when": {"public_disclosed": {"value": False}}})
    m["cases"][0]["extraction_state"] = "incomplete"
    setup = fill(refresh(setup))
    checked = validate(setup, proposal(setup))
    assert "left:policy_blocks_candidate" in checked["rows"][0]["issue_codes"]
    assert "left:extraction_incomplete" in checked["adjudication_rows"][0]["original_issue_codes"]
    assert "policy_blocks_candidate" in checked["adjudication_rows"][0]["proposal_findings"]


def test_same_slot_rejected(setup):
    m, c, a, _ = setup
    with pytest.raises(FactContractError, match="distinct_slots"):
        compare_submissions(m, a, a, context=c)


def test_assignment_intersection_is_not_a_comparison_population(setup):
    setup = second_case(setup)
    setup[0]["assignments"][1]["case_ids"].pop()
    setup = refresh(setup)
    with pytest.raises(FactContractError, match="assignment_coverage_mismatch"):
        compare(setup)


@pytest.mark.parametrize("target,field,value", [
    ("batch", "org_id", "another-org"), ("batch", "job_id", "another-job"),
    ("batch", "manifest_sha256", "0" * 64), ("row", "case_sha256", "0" * 64),
    ("row", "packet_sha256", "0" * 64), ("row", "policy_sha256", "0" * 64),
    ("decision", "rule_ids", ["nonexistent"]), ("decision", "grade", "TS"),
])
def test_both_submissions_revalidated(setup, target, field, value):
    fill(setup)
    selected = {"batch": setup[3], "row": setup[3]["rows"][0], "decision": setup[3]["rows"][0]["decision"]}[target]
    selected[field] = value
    with pytest.raises(FactContractError):
        compare(setup)


@pytest.mark.parametrize("part", ["policy", "view", "revision", "origin"])
def test_changed_manifest_rejects_old_pair(setup, part):
    fill(setup)
    m, c, a, b = setup
    if part == "policy":
        m["policy_id"] = "changed-policy-identity"
    elif part == "view":
        m["cases"][0]["input_view"] = "body_and_evidence"
    elif part == "revision":
        m["cases"][0]["document"]["document_revision"] = "r2"
    else:
        m["cases"][0]["family_id"] = "new-declared-family"
    context = c.model_copy(update={"manifest_sha256": manifest_digest(m)})
    with pytest.raises(FactContractError, match="binding_mismatch"):
        compare_submissions(m, a, b, context=context)


def test_empty_cases_not_success(setup):
    setup[0]["cases"] = []
    with pytest.raises(FactContractError):
        compare(setup)


def test_deterministic_no_mutation_no_private_text_or_actors(setup):
    fill(setup)
    proposed = proposal(setup)
    before = copy.deepcopy((setup, proposed))
    first = validate(setup, proposed)
    assert first == validate(setup, proposed)
    rendered = json.dumps(first)
    for secret in ("fictional private", "fictional-not-signoff", "fictional-third-person", "approved_only", "fictional reason"):
        assert secret not in rendered
    assert (setup, proposed) == before
    first["rows"][0]["issue_codes"].append("mutated-output")
    assert "mutated-output" not in json.dumps(compare(setup))
    template = blank(setup)
    assert template["rows"][0]["decision"] is None
    assert template["rows"][0]["cause_codes"] == []
    assert template["rows"][0]["adjudicator_ref"] is None


@pytest.mark.parametrize("side", [2, 3])
def test_changed_submission_invalidates_proposal_even_same_grade(setup, side):
    fill(setup)
    proposed = proposal(setup)
    setup[side]["rows"][0]["decision"]["rationale"] = "changed explanation"
    with pytest.raises(FactContractError, match="pair_binding_mismatch"):
        validate(setup, proposed)


def test_reversed_roles_invalidate_old_proposal(setup):
    fill(setup)
    proposed = proposal(setup)
    m, c, a, b = setup
    with pytest.raises(FactContractError, match="pair_binding_mismatch"):
        validate_adjudications(m, b, a, proposed, context=c)


def test_rechecking_later_does_not_change_pair_identity(setup):
    fill(setup)
    proposed = proposal(setup)
    m, c, a, b = setup
    later = c.model_copy(update={"as_of": "2026-09-15T04:00:00Z"})
    assert compare_submissions(m, a, b, context=later)["pair_sha256"] == proposed["pair_sha256"]
    assert validate_adjudications(m, a, b, proposed, context=later)["proposed_count"] == 1


@pytest.mark.parametrize("field", ["pair_sha256", "case_sha256", "left_row_sha256", "right_row_sha256"])
def test_proposal_hashes_checked(setup, field):
    fill(setup)
    proposed = proposal(setup)
    (proposed if field == "pair_sha256" else proposed["rows"][0])[field] = "0" * 64
    with pytest.raises(FactContractError, match="binding_mismatch"):
        validate(setup, proposed)


@pytest.mark.parametrize("kind", ["empty", "duplicate", "unknown", "missing"])
def test_proposals_exact_coverage(setup, kind):
    setup = fill(second_case(setup))
    proposed = proposal(setup)
    if kind == "empty":
        proposed["rows"] = []
    elif kind == "duplicate":
        proposed["rows"].append(copy.deepcopy(proposed["rows"][0]))
    elif kind == "unknown":
        proposed["rows"][0]["case_id"] = "not-assigned"
    else:
        proposed["rows"].pop()
    with pytest.raises(FactContractError):
        validate(setup, proposed)


@pytest.mark.parametrize("field", ["adjudicator_ref", "actor_kind", "proposed_at", "reviewed_both", "resolution_note", "decision", "cause_codes"])
def test_proposal_required_fields(setup, field):
    fill(setup)
    proposed = proposal(setup)
    proposed["rows"][0][field] = [] if field == "cause_codes" else None
    with pytest.raises(FactContractError, match="adjudication_incomplete"):
        validate(setup, proposed)


@pytest.mark.parametrize("field", ["adjudicator_ref", "actor_kind", "proposed_at", "reviewed_both", "resolution_note", "decision", "cause_codes"])
def test_pending_cannot_contain_hidden_proposal(setup, field):
    fill(setup)
    empty = blank(setup)
    empty["rows"][0][field] = proposal(setup)["rows"][0][field]
    with pytest.raises(FactContractError, match="pending_adjudication_contains_proposal"):
        validate(setup, empty)


@pytest.mark.parametrize("at", ["2026-09-15T02:29:59Z", "2026-09-15T03:00:01Z", "2026-09-15T02:45:00"])
def test_proposal_time_after_both_submissions(setup, at):
    fill(setup)
    proposed = proposal(setup)
    proposed["rows"][0]["proposed_at"] = at
    with pytest.raises(FactContractError, match="time"):
        validate(setup, proposed)


@pytest.mark.parametrize("side", [2, 3])
def test_adjudicator_distinct_claim_from_reviewers(setup, side):
    fill(setup)
    proposed = proposal(setup)
    proposed["rows"][0]["adjudicator_ref"] = setup[side]["rows"][0]["reviewer_ref"]
    with pytest.raises(FactContractError, match="adjudicator_matches_reviewer"):
        validate(setup, proposed)


def test_proposal_requires_both_submissions(setup):
    empty = copy.deepcopy(setup[3]["rows"][0])
    fill(setup)
    proposed = proposal(setup)
    setup[3]["rows"][0] = empty
    fresh = blank(setup)
    proposed.update(binding=fresh["binding"], pair_sha256=fresh["pair_sha256"])
    proposed["rows"][0]["right_row_sha256"] = fresh["rows"][0]["right_row_sha256"]
    with pytest.raises(FactContractError, match="needs_both_submissions"):
        validate(setup, proposed)


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "empty_reason", "invalid_action"])
def test_every_detected_issue_needs_a_response(setup, mutation):
    fill(setup)
    setup[3]["rows"][0]["decision"]["rationale"] = "different"
    proposed = proposal(setup)
    responses = proposed["rows"][0]["issue_responses"]
    if mutation == "missing":
        responses.clear()
    elif mutation == "extra":
        responses.append({"issue_code": "invented", "action": "retain_for_review", "reason": "fictional"})
    elif mutation == "duplicate":
        responses.append(copy.deepcopy(responses[0]))
    elif mutation == "empty_reason":
        responses[0]["reason"] = " "
    else:
        responses[0]["action"] = "resolved_and_approved"
    with pytest.raises(FactContractError):
        validate(setup, proposed)


@pytest.mark.parametrize("action", ["request_new_input", "request_policy_change"])
def test_requested_changes_cannot_be_presented_as_grade_proposal(setup, action):
    fill(setup)
    setup[3]["rows"][0]["decision"]["rationale"] = "different"
    proposed = proposal(setup)
    proposed["rows"][0]["issue_responses"][0]["action"] = action
    with pytest.raises(FactContractError, match="pending_change_cannot_propose_grade"):
        validate(setup, proposed)
    hold(proposed["rows"][0], "policy_gap")
    assert validate(setup, proposed)["adjudication_rows"][0]["decision_status"] == "policy_gap"


@pytest.mark.parametrize("field,value", [("cause_codes", ["unknown"]), ("cause_codes", ["label_error", "label_error"]),
                                       ("reviewed_both", False), ("reviewed_both", 1), ("state", "finalized")])
def test_cause_and_state_contract(setup, field, value):
    fill(setup)
    proposed = proposal(setup)
    proposed["rows"][0][field] = value
    with pytest.raises(FactContractError):
        validate(setup, proposed)


def test_ai_assisted_adjudication_remains_declared(setup):
    fill(setup)
    proposed = proposal(setup)
    proposed["rows"][0]["actor_kind"] = "ai_assisted_declared"
    checked = validate(setup, proposed)
    assert "adjudication_ai_assistance_declared" in checked["adjudication_rows"][0]["proposal_findings"]
    assert checked["identity_authenticated"] is False


def test_root_cause_can_remain_undetermined(setup):
    fill(setup)
    setup[3]["rows"][0]["decision"]["rationale"] = "different interpretation"
    proposed = proposal(setup)
    proposed["rows"][0]["cause_codes"] = ["undetermined"]
    proposed["rows"][0]["issue_responses"][0]["action"] = "retain_for_review"
    result = validate(setup, proposed)
    assert result["adjudication_rows"][0]["cause_codes_claimed"] == ["undetermined"]
    assert result["root_causes_verified"] is False


def test_partially_filled_proposals_preserve_pending_counts(setup):
    setup = fill(second_case(setup))
    proposed = proposal(setup)
    proposed["rows"][1] = blank(setup)["rows"][1]
    result = validate(setup, proposed)
    assert result["proposed_count"] == result["pending_proposal_count"] == 1
    assert result["status"] == "awaiting_adjudication_proposals"


def test_proposal_batch_cannot_mix_adjudicator_claims(setup):
    setup = fill(second_case(setup))
    proposed = proposal(setup)
    proposed["rows"][1]["adjudicator_ref"] = "other-third-person"
    with pytest.raises(FactContractError, match="multiple_identities"):
        validate(setup, proposed)


@pytest.mark.parametrize("mutation", ["rule", "citation", "grade"])
def test_proposed_decision_reuses_r2_policy_and_evidence_checks(setup, mutation):
    fill(setup)
    proposed = proposal(setup)
    decision = proposed["rows"][0]["decision"]
    if mutation == "rule":
        decision["rule_ids"] = ["unknown"]
    elif mutation == "citation":
        decision["evidence"][0]["reference"]["value_sha256"] = "0" * 64
    else:
        decision["grade"] = "TS"
    with pytest.raises(FactContractError):
        validate(setup, proposed)


@pytest.mark.parametrize("permission", ["unknown_real", "denied"])
def test_template_permission_boundary(setup, permission):
    m = setup[0]
    if permission == "denied":
        m["authorization"]["status"] = "denied"
    else:
        m["cases"][0]["source_origin"] = "customer_real"
        m["cases"][0]["packet"]["material_role"] = "unverified_supplied_assertions"
    # Refresh bindings without exporting a new real/denied reviewer worksheet.
    m["cases"][0]["packet_sha256"] = value_digest(m["cases"][0]["packet"])
    m, c, a, b = setup
    c = c.model_copy(update={"manifest_sha256": manifest_digest(m)})
    case_sha = value_digest(m["cases"][0])
    for batch in (a, b):
        batch["manifest_sha256"] = c.manifest_sha256
        batch["rows"][0].update(case_sha256=case_sha, packet_sha256=m["cases"][0]["packet_sha256"])
    setup = m, c, a, b
    assert compare(setup)["missing_claims"]
    with pytest.raises(FactContractError, match="permission"):
        blank(setup)


def test_constructed_model_cannot_bypass_validation(setup):
    proposed = AdjudicationBatch.model_construct(**{**blank(setup), "rows": []})
    with pytest.raises(FactContractError):
        validate(setup, proposed)


def write_inputs(tmp_path, setup):
    paths = []
    for name, value in zip(("manifest", "context", "left", "right"), setup, strict=True):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(value.model_dump() if name == "context" else value), encoding="utf-8")
        paths.extend(["--" + name, str(path)])
    return paths


def test_cli_blank_and_proposal_check(tmp_path, setup, capsys):
    args = write_inputs(tmp_path, fill(setup))
    template, report = tmp_path / "blank.json", tmp_path / "report.json"
    assert cli.main([*args, "--template-out", str(template), "--out", str(report)]) == 3
    assert json.loads(template.read_text())["rows"][0]["decision"] is None
    assert "implementation_sha256" in json.loads(report.read_text())
    assert cli.main([*args, "--adjudications", str(template)]) == 3
    proposed_path = tmp_path / "proposal.json"
    proposed_path.write_text(json.dumps(proposal(setup)), encoding="utf-8")
    assert cli.main([*args, "--adjudications", str(proposed_path)]) == 3
    assert "adjudication_proposals_checked_review_required" in capsys.readouterr().out
    before = template.read_bytes()
    assert cli.main([*args, "--template-out", str(template)]) == 2
    assert template.read_bytes() == before


@pytest.mark.parametrize("args", [[], ["--demo", "--schema"], ["--demo", "--left", "absent.json"],
                                 ["--schema", "--template-out", "new.json"],
                                 ["--template-out", "new.json", "--adjudications", "absent.json"]])
def test_cli_mode_errors(args, capsys):
    assert cli.main(args) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "INVALID_REVIEW_PAIR_INPUT"


@pytest.mark.parametrize("bad", ['{"secret":"DO_NOT_PRINT","secret":1}', '{"x":NaN}', '{invalid'])
def test_cli_strict_json_no_payload_on_errors(tmp_path, setup, capsys, bad):
    args = write_inputs(tmp_path, setup)
    (tmp_path / "right.json").write_text(bad, encoding="utf-8")
    assert cli.main(args) == 2
    assert "DO_NOT_PRINT" not in capsys.readouterr().out


def test_cli_input_mutation_detected_before_write(tmp_path, setup, monkeypatch, capsys):
    args = write_inputs(tmp_path, setup)
    original = cli.compare_submissions
    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        (tmp_path / "left.json").write_text("{}", encoding="utf-8")
        return result
    monkeypatch.setattr(cli, "compare_submissions", mutate)
    out = tmp_path / "report.json"
    assert cli.main([*args, "--out", str(out)]) == 2
    assert not out.exists() and "input_changed_during_check" in capsys.readouterr().out


def test_cli_output_overlap(tmp_path, setup):
    args = write_inputs(tmp_path, setup)
    assert cli.main([*args, "--out", args[1]]) == 2
    out = str(tmp_path / "same.json")
    assert cli.main([*args, "--out", out, "--template-out", out]) == 2


def test_cli_demo_schema_are_not_real_work(tmp_path, capsys):
    assert cli.main(["--demo"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["human_submissions"] == report["real_documents"] == report["adjudications"] == 0
    assert all(r["agreement_all_completed"]["grade_agreement"]["rate"] is None for r in report["results"])
    path = tmp_path / "schema.json"
    assert cli.main(["--schema", "--out", str(path)]) == 0
    assert json.loads(path.read_text())["cross_field_checks_required"] is True
