"""Source-anchored arithmetic and policy boundaries; no model quality claims."""
from __future__ import annotations

import copy
import json
from collections import Counter
from itertools import product

import pytest

import build_customer_guide_reference as cli
from customer_benchmark_drafts import build_drafts
from customer_guide_cases import SETTINGS, build_reference, premises
from koipa import customer_guide_reference as guide
from koipa.customer_benchmark import FLAGS
from koipa.dataset_usage import assert_dataset_usage
from koipa.policy_facts import FactContractError, value_digest


@pytest.mark.parametrize("s,v,m", list(product(range(3), repeat=3)))
def test_27_formula_combinations(s, v, m):
    # Enumerated set membership, not the production product dictionary.
    ts = {(2, 2, 2)}
    s1 = {(1, 2, 2), (2, 1, 2), (2, 2, 1)}
    if 0 in (s, v, m):
        expected = "S3"
    elif (s, v, m) in ts:
        expected = "TS"
    elif (s, v, m) in s1:
        expected = "S1"
    else:
        expected = "S2"
    assert guide.grade_from_levels(s, v, m) == expected


@pytest.mark.parametrize("value", [True, False, "2", 2.0, None, -1, 3])
def test_invalid_factor_numbers(value):
    with pytest.raises(FactContractError):
        guide.grade_from_levels(value, 2, 2)


def base():
    return premises(SETTINGS["thermal-window"])


@pytest.mark.parametrize("field", guide.REQUIRED)
def test_any_required_unknown_holds_even_if_product_would_be_zero(field):
    f = premises(SETTINGS["product-help"])
    f[field] = None
    r = guide.decide(f)
    assert r["status"] == "HOLD" and r["reference_grade"] is None
    assert field in r["missing_evidence"]


@pytest.mark.parametrize("cost,hours,utility,expected", [
    (0, 0, False, 0), (0, 0, True, None), (1, 0, True, 1), (0, 1, True, 1),
    (1000000, 40, True, 1), (1000001, 40, True, None), (1000000, 41, True, None),
    (29999999, 479, True, None), (30000000, 0, True, 2), (0, 480, True, 2),
    (30000000, 480, True, 2), (100, 1, False, None)])
def test_local_value_boundaries(cost, hours, utility, expected):
    f = base()
    f.update(cost_krw=cost, person_hours=hours, economic_utility=utility)
    r = guide.decide(f)
    assert r["factors"]["V"] == expected
    assert (r["status"] == "HOLD") == (expected is None)


@pytest.mark.parametrize("change", [
    {"public_exact_body": True}, {"ordinary_access_difficult": False},
    {"investment_scope_exact": False}, {"all_staff_knows": True},
    {"secrecy_manageable": False}, {"access_enforced": False}, {"business_need_only": False}])
def test_conflicting_or_unsupported_premises_hold(change):
    f = base()
    f.update(change)
    assert guide.decide(f)["status"] == "HOLD"


@pytest.mark.parametrize("key,value", [("cost_krw", True), ("cost_krw", 2.5), ("cost_krw", -1),
    ("cost_krw", 10**13), ("public_exact_body", 0), ("individual_approval", "yes")])
def test_premise_strict_types(key, value):
    f = base()
    f[key] = value
    with pytest.raises(FactContractError):
        guide.decide(f)


def test_zero_management_is_not_disclosure_permission():
    f = base()
    f.update(all_staff_knows=True, individual_approval=False, business_need_only=False, access_enforced=False)
    r = guide.decide(f)
    assert r["factors"] == {"S": 2, "V": 2, "M": 0} and r["reference_grade"] == "S3"
    assert r["separate_disclosure_review_required"] is True
    assert r["disclosure_permission_granted_by_classifier"] is False


def test_disclosure_unknown_does_not_fake_permission():
    f = premises(SETTINGS["product-help"])
    f.update(release_authorized=None, other_risk_present=None)
    r = guide.decide(f)
    assert r["reference_grade"] == "S3" and r["separate_disclosure_review_required"]


def test_twenty_answers_recompute_and_old_bodies_remain_identical():
    before = build_drafts()
    docs, answers, details = build_reference()
    parsed = guide.validate_reference(docs, answers, details)
    assert len(parsed) == 20 and len({d.input.text for d in parsed}) == 20
    assert Counter(a["reference_grade"] for a in answers) == {"TS": 4, "S1": 4, "S2": 6, "S3": 6}
    for old, new in zip(before, docs[:12], strict=True):
        assert old["input"]["text"] == new["input"]["text"]
        assert old["family_id"] == new["family_id"]
        assert old["input"]["doc_id"] != new["input"]["doc_id"]
    assert before == build_drafts()
    assert len({a["policy_sha256"] for a in answers}) == 1
    assert all(d["reference_status"] == "internally_fixed_conditional" for d in details)
    # Values, not repeated textual grade explanations, are the model's premises.
    for d in parsed:
        assert guide.decode_context([c.model_dump() for c in d.input.context])
        for forbidden in ("reference_grade", "rule_ids", "SVM", "TS", "S1", "S2", "S3"):
            assert forbidden not in json.dumps(d.input.model_dump(), ensure_ascii=False)


@pytest.mark.parametrize("purpose", ["training", "model_evaluation"])
def test_candidate_pack_is_not_a_training_permission(purpose):
    with pytest.raises(ValueError):
        assert_dataset_usage(build_reference()[0], purpose=purpose)


@pytest.mark.parametrize("mutation", ["answer", "formula_mode", "policy_hash", "rule", "factor", "premise", "context_evidence",
                                      "body_hash", "input_hash", "body_only", "gold", "flag", "detail_duplicate", "empty"])
def test_reference_mutations_are_rejected(mutation):
    docs, answers, details = build_reference()
    if mutation == "answer":
        answers[0]["reference_grade"] = "TS"
    elif mutation == "formula_mode":
        answers[0]["policy_id"] = "v22"
    elif mutation == "policy_hash":
        answers[0]["policy_sha256"] = "0"*64
    elif mutation == "rule":
        answers[0]["rule_ids"][0] = "made-up-rule"
    elif mutation == "factor":
        details[0]["decision"]["factors"]["S"] = 0
    elif mutation == "premise":
        details[0]["premises"]["cost_krw"] = 0
    elif mutation == "context_evidence":
        details[0]["context_evidence"][0]["sha256"] = "0"*64
    elif mutation in {"body_hash", "input_hash"}:
        details[0]["body_sha256" if mutation == "body_hash" else "input_sha256"] = "0"*64
    elif mutation == "body_only":
        details[0]["body_only_grade_scoring_allowed"] = True
    elif mutation == "gold":
        details[0]["reference_status"] = "human_gold"
    elif mutation == "flag":
        details[0]["training_allowed"] = 0
    elif mutation == "detail_duplicate":
        details[1] = copy.deepcopy(details[0])
    else:
        docs, answers, details = [], [], []
    with pytest.raises(FactContractError):
        guide.validate_reference(docs, answers, details)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "unknown_label", "bool", "int", "origin", "extra"])
def test_context_codec_is_fail_closed(mutation):
    context = guide.encode_context(base())
    if mutation == "missing":
        context.pop()
    elif mutation == "duplicate":
        context[1] = copy.deepcopy(context[0])
    elif mutation == "unknown_label":
        context[0]["value"] += "; S: 2"
    elif mutation == "bool":
        context[0]["value"] = context[0]["value"].replace("아니오", "0", 1)
    elif mutation == "int":
        context[1]["value"] = context[1]["value"].replace("36000000", "036000000")
    elif mutation == "origin":
        context[0]["origin"] = "actual_customer"
    else:
        context[0]["label"] = "TS"
    with pytest.raises((FactContractError, KeyError)):
        guide.decode_context(context)


def test_arithmetic_counterexamples_and_formula_are_not_model_scores():
    docs, payload = cli.core_payload()
    arithmetic = cli.arithmetic_checks(docs)
    assert arithmetic["passed"] == 11
    probes = json.loads(payload["audit/body_only_counterexamples.json"])
    assert probes["changed_grade_same_body"] == 20 and probes["new_documents"] == 0
    assert guide.formula_audit()["passed"] == 27
    assert json.loads(payload["summary.json"])["classification_accuracy"] is None


def test_corrupted_arithmetic_fails_even_after_rebinding_document():
    docs = cli.core_payload()[0]
    raw = docs[0].model_dump()
    raw["input"]["text"] = raw["input"]["text"].replace("100mm에서는 26", "100mm에서는 27")
    # Explicitly construct a local validation probe, never export it as a document.
    docs[0] = docs[0].model_copy(update={"input": docs[0].input.model_copy(update={"text": raw["input"]["text"]})})
    with pytest.raises(FactContractError, match="arithmetic_mismatch"):
        cli.arithmetic_checks(docs)


def test_pack_build_replay_and_nonoverwrite(tmp_path):
    out = tmp_path/"pack"
    result = cli.prepare(out)
    assert result["internally_fixed_conditional_answers"] == 20
    assert cli.verify(out) == result
    assert len(list((out/"documents").glob("*.txt"))) == 20
    assert cli.main(["prepare", "--out", str(out)]) == 2
    assert cli.main(["verify", "--pack", str(tmp_path/"missing")]) == 2


@pytest.mark.parametrize("mutation", ["hash", "grade_rehashed", "missing", "extra", "path_escape", "permission", "source", "body_only_rehashed"])
def test_pack_tampering_fails(tmp_path, mutation):
    out = tmp_path/"pack"
    cli.prepare(out)
    manifest_path = out/"manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if mutation == "hash":
        manifest["files"]["summary.json"] = "0"*64
    elif mutation in {"grade_rehashed", "body_only_rehashed"}:
        path = "answers/answers.candidate.jsonl" if mutation == "grade_rehashed" else "answers/evidence.jsonl"
        rows = cli._rows((out/path).read_bytes())
        rows[0]["reference_grade" if mutation == "grade_rehashed" else "body_only_grade_scoring_allowed"] = "TS" if mutation == "grade_rehashed" else True
        data = cli._jsonl(rows)
        (out/path).write_text(data, encoding="utf-8", newline="\n")
        manifest["files"][path] = cli.text_digest(data)
    elif mutation == "missing":
        del manifest["files"]["summary.json"]
    elif mutation == "extra":
        (out/"unlisted.txt").write_text("probe", encoding="utf-8")
    elif mutation == "path_escape":
        manifest["files"]["../outside.txt"] = "0"*64
    elif mutation == "permission":
        manifest["training_allowed"] = 0
    elif mutation == "source":
        manifest["source_files_sha256"] = {}
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    with pytest.raises((FactContractError, OSError)):
        cli.verify(out)


def test_policy_anchors_are_identified_as_local_not_pdf():
    assert guide.POLICY["source"]["criteria_page"] == 11
    assert guide.POLICY["source"]["formula_page"] == 12
    assert "local_anchors_not_from_pdf" in guide.POLICY
    assert value_digest(guide.POLICY) == guide.POLICY_SHA256
    assert all(value is False for value in FLAGS.values())
