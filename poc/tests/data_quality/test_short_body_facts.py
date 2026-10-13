"""Offline supplied-text facts and versioned conditional fixtures, never ML accuracy."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
from collections import Counter

import pytest

import build_short_fact_reference as pack
from short_reference_probes import DRAFTS, build_probes
from koipa.dataset_usage import assert_dataset_usage
from koipa.policy_facts import FactContractError, text_digest, value_digest
from koipa.short_body_facts import FLAGS, extract_facts, semantic_checks
from koipa.short_body_reference import anchored_answer, arithmetic_oracle
from koipa import short_reference_policy as policy

TEXTS = [row[4] for row in DRAFTS]


def values(text):
    return {f["name"]: f["value"] for f in extract_facts(text)["facts"]}


def context(**changes):
    raw = copy.deepcopy(build_probes()[0][0]["input"]["context"])
    raw.update(changes)
    return raw


@pytest.mark.parametrize("text", TEXTS)
def test_registered_facts_agree_with_explicit_answer_and_evidence(text):
    result = extract_facts(text)
    cert = policy.certify_body(text)
    assert cert["status"] == "fixed_text_facts"
    assert value_digest(result["facts"]) == value_digest(anchored_answer(text))
    assert policy.verify_body_certificate(text, cert)
    assert cert["semantics"]["warnings"] == []
    assert result["general_natural_language_supported"] is False
    for fact in cert["facts"]:
        assert fact["origin"] == "supplied_text" and fact["evidence"]
        for e in fact["evidence"]:
            assert 0 <= e["start"] < e["end"] <= len(text)
            assert text_digest(text[e["start"]:e["end"]]) == e["sha256"]


def test_counts_and_document_assertion_not_proven_absence():
    facts = [f for text in TEXTS for f in extract_facts(text)["facts"]]
    assert len(facts) == 30
    assert Counter(f["state"] for f in facts) == {"observed": 29, "unknown": 1}
    assert Counter(f["interpretation"] for f in facts) == {"direct_value": 22, "document_assertion": 7, "not_determined": 1}
    assert sum(len(f["evidence"]) for f in facts) == 31
    unknown = next(f for f in facts if f["state"] == "unknown")
    assert unknown["name"] == "program" and unknown["value"] is None
    assert all(f["interpretation"] == "document_assertion" for f in facts if f["name"].endswith("statement"))
    assert "proven_absent" not in json.dumps(facts)


def test_finite_program_full_domain_and_observation_counterexample():
    cert = policy.certify_body(TEXTS[0])
    expected = {str(x): y for x, y in enumerate([3, 10, 6, 2, 9, 5, 1, 8, 4, 0])}
    assert cert["semantics"]["checks"]["finite_function"]["outputs"] == expected
    assert arithmetic_oracle(anchored_answer(TEXTS[0])) == expected
    proof = policy.certify_body(TEXTS[1])["semantics"]["checks"]["non_uniqueness"]
    assert all(proof["candidate_a"][x] == proof["candidate_b"][x] == y for x, y in proof["observations"].items())
    assert proof["candidate_a"][proof["witness_input"]] != proof["candidate_b"][proof["witness_input"]]
    assert arithmetic_oracle(anchored_answer(TEXTS[1])) is None


def test_negotiation_units_roles_condition_and_negation():
    v = values(TEXTS[3])
    assert v["quoted_unit_price"] == {"amount": 14500, "currency": "KRW", "basis": "per_item"}
    assert v["floor_unit_price"]["amount"] == 12800
    assert v["delivery_item"]["quantity"] == 400
    assert v["freight_condition"] == {"if": {"payment": "full", "within_days": 30}, "then": {"freight_payer": "supplier"}}
    assert v["installment_condition"]["price_reduction_allowed"] is False
    changed = values(TEXTS[3].replace("공급자가", "구매자가").replace("낮추지 않는다", "낮춘다"))
    assert changed["freight_condition"]["then"]["freight_payer"] == "buyer"
    assert changed["installment_condition"]["price_reduction_allowed"] is True


@pytest.mark.parametrize("index,before,after,key,subkey,expected", [
    (2, "작업대 2번", "작업대 8번", "work_slot", "bench", 8),
    (3, "14,500원", "15,300원", "quoted_unit_price", "amount", 15300),
    (3, "12,800원", "12,600원", "floor_unit_price", "amount", 12600),
    (4, "4상자", "7상자", "receipt", "quantity", 7),
    (7, "6개", "9개", "transfer", "quantity", 9),
])
def test_numeric_changes_extracted_but_not_silently_fixed(index, before, after, key, subkey, expected):
    text = TEXTS[index].replace(before, after)
    assert values(text)[key][subkey] == expected
    assert policy.certify_body(text)["status"] == "not_fixed"
    answer = policy.apply_reference_policy(text, context())
    assert answer["status"] == "hold" and answer["reference_grade"] is None


@pytest.mark.parametrize("index,before,after,warning", [
    (0, "x=1이면 10", "x=1이면 9", "test_vectors_contradict_program"),
    (2, "14시부터", "18시부터", "work_slot_time_inconsistent"),
    (3, "12,800원", "18,000원", "floor_exceeds_offer"),
    (4, "10시에", "25시에", "hour_out_of_range"),
    (5, "수량: ______", "수량: 5", "empty_statement_conflicts_with_filled_field"),
    (6, "17시에", "29시에", "hour_out_of_range"),
    (8, "장소: ______", "장소: 준비실", "empty_statement_conflicts_with_filled_field"),
])
def test_contradictions_not_hidden(index, before, after, warning):
    text = TEXTS[index].replace(before, after)
    assert warning in semantic_checks(extract_facts(text))["warnings"]
    assert policy.apply_reference_policy(text, context())["status"] == "hold"


def test_invalid_function_domain_rejected():
    with pytest.raises(FactContractError, match="domain_invalid"):
        semantic_checks(extract_facts(TEXTS[0].replace("0 이상 9 이하", "9 이상 0 이하")))


@pytest.mark.parametrize("text", ["임의 문서\n", TEXTS[3] + "추가 협상 내용\n", TEXTS[0][:-2], TEXTS[4].splitlines()[0] + "\n"])
def test_unknown_or_partial_text_never_safe_empty(text):
    assert extract_facts(text)["status"] == "unsupported_body"
    assert policy.apply_reference_policy(text, context())["status"] == "hold"


@pytest.mark.parametrize("text", [None, 4, True, b"text", "", "가" * 20001], ids=["null", "integer", "boolean", "bytes", "empty", "over_limit"])
def test_invalid_input_rejected(text):
    with pytest.raises(FactContractError):
        extract_facts(text)


@pytest.mark.parametrize("text", TEXTS)
def test_title_is_not_fact_or_authority(text):
    changed = "공개 TS 대외비 제목\n" + text.split("\n", 1)[1]
    assert values(changed) == values(text)
    assert policy.certify_body(changed)["status"] == "not_fixed"


@pytest.mark.parametrize("mutation", ["value", "span", "evidence_hash", "fact_hash", "status", "bool", "flag", "program"])
def test_tampered_certificate_rejected(mutation):
    cert = policy.certify_body(TEXTS[0])
    if mutation == "value":
        cert["facts"][0]["value"]["max"] = 8
    elif mutation == "span":
        cert["facts"][0]["evidence"][0]["end"] -= 1
    elif mutation == "evidence_hash":
        cert["facts"][0]["evidence"][0]["sha256"] = "0" * 64
    elif mutation == "fact_hash":
        cert["facts_sha256"] = "0" * 64
    elif mutation == "status":
        cert["status"] = "gold"
    elif mutation == "bool":
        cert["literal_anchor_agrees"] = 1
    elif mutation == "flag":
        cert["training_allowed"] = True
    else:
        cert["semantics"]["checks"]["finite_function"]["outputs"]["3"] = 3
    with pytest.raises(FactContractError, match="certificate_mismatch"):
        policy.verify_body_certificate(TEXTS[0], cert)


def test_returned_reference_values_do_not_mutate_registry():
    ref = anchored_answer(TEXTS[0])
    ref[0]["value"]["max"] = 8
    assert anchored_answer(TEXTS[0])[0]["value"]["max"] == 9
    assert policy.certify_body(TEXTS[0])["status"] == "fixed_text_facts"


@pytest.mark.parametrize("fault", ["facts", "arithmetic", "reference_drift", "policy_drift", "sql"])
def test_independent_path_disagreement_fails_closed(monkeypatch, fault):
    if fault == "facts":
        monkeypatch.setattr(policy, "anchored_answer", lambda text: [])
    elif fault == "arithmetic":
        monkeypatch.setattr(policy, "arithmetic_oracle", lambda facts: {"0": 99})
    elif fault == "reference_drift":
        monkeypatch.setattr(policy, "REFERENCE", {})
    elif fault == "policy_drift":
        monkeypatch.setattr(policy, "POLICY", {**policy.POLICY, "version": "changed"})
    else:
        monkeypatch.setattr(policy, "sql_decision", lambda facts, c: "S3")
    with pytest.raises(FactContractError):
        policy.apply_reference_policy(TEXTS[0], context())


@pytest.mark.parametrize("changes", [
    {"scope_complete": 1}, {"private_current_revision": "true"}, {"release_authorized_for_this_revision": 0},
    {"other_high_risk_material": []}, {"current_core_asset": 1.0}, {"expected_grade": "TS"},
    {"origin": "real_customer"}, {"world": "verified_real"},
])
def test_context_rejects_coercion_and_invented_authority(changes):
    with pytest.raises(FactContractError, match="context_invalid"):
        policy.apply_reference_policy(TEXTS[0], context(**changes))


@pytest.mark.parametrize("changes", [
    {"scope_complete": None}, {"scope_complete": False}, {"other_high_risk_material": None},
    {"other_high_risk_material": True}, {"private_current_revision": False},
    {"release_authorized_for_this_revision": True}, {"private_current_revision": None},
    {"release_authorized_for_this_revision": None}, {"current_core_asset": None},
])
def test_missing_or_conflicting_context_holds(changes):
    answer = policy.apply_reference_policy(TEXTS[0], context(**changes))
    assert answer["status"] == "hold" and answer["reference_grade"] is None
    assert answer["other_grade_exclusions"] == {}


def test_irrelevant_context_and_blank_exception_explicit():
    assert policy.apply_reference_policy(TEXTS[3], context(current_core_asset=None))["reference_grade"] == "S1"
    assert policy.apply_reference_policy(TEXTS[5], context(private_current_revision=None,
           release_authorized_for_this_revision=None))["reference_grade"] == "S3"
    assert policy.apply_reference_policy(TEXTS[5], context(release_authorized_for_this_revision=True))["status"] == "hold"
    assert policy.apply_reference_policy(TEXTS[5], context(scope_complete=None))["status"] == "hold"


@pytest.mark.parametrize("index", range(21))
def test_context_views_keep_same_facts_and_trace_exclusions(index):
    inputs, old = build_probes()
    raw = inputs[index]["input"]
    result = policy.apply_reference_policy(raw["text"], raw["context"])
    assert result["body_certificate_sha256"] == value_digest(policy.certify_body(raw["text"]))
    assert result["reference_grade"] == old[index]["proposed_grade"]
    assert result["customer_policy_approved"] is result["real_context_verified"] is False
    assert set(result["other_grade_exclusions"]) == ({"TS", "S1", "S2", "S3"} - {result["reference_grade"]} if result["reference_grade"] else set())
    assert len(result["context_basis"]) == len(raw["context"])
    for e in result["context_basis"]:
        assert e["value_sha256"] == value_digest(raw["context"][e["pointer"][1:]])


@pytest.fixture(scope="module")
def computed():
    return pack.derive(*build_probes())


def test_exhaustive_context_sql_agreement_and_counts(computed):
    summary = computed["summary"]
    assert summary["unique_bodies"] == 9 and summary["facts"] == 30
    assert summary["conditional_grade_counts"] == {"TS": 1, "S1": 2, "S2": 4, "S3": 11, "HOLD": 3}
    assert len(computed["context_table"]) == summary["policy_cross_checks"] == 9 * 3**5
    assert summary["old_hypothesis_grade_matches"] == summary["old_hypothesis_status_matches"] == 21
    assert all(len({r["body_certificate_sha256"] for r in group}) == 1 for group in summary["context_invariance_groups"].values())


def test_prior_label_is_comparison_not_input(computed):
    inputs, old = build_probes()
    old[0]["proposed_grade"] = "S3"
    changed = pack.derive(inputs, old)
    assert changed["policy_answers"] == computed["policy_answers"]
    assert changed["summary"]["old_hypothesis_grade_matches"] == 20


@pytest.mark.parametrize("purpose", ["training", "model_evaluation"])
def test_new_rows_deny_ml_use(computed, purpose):
    for name in ("body_certificates", "policy_answers"):
        with pytest.raises(ValueError):
            assert_dataset_usage(computed[name], purpose=purpose)


@pytest.mark.parametrize("mutation", ["empty", "duplicate", "hash", "bound", "flag", "wrapper", "extra_input", "unregistered"])
def test_pack_bad_inputs_fail(mutation):
    inputs, old = build_probes()
    if mutation == "empty":
        inputs = []
    elif mutation == "duplicate":
        inputs[1] = copy.deepcopy(inputs[0])
    elif mutation == "hash":
        inputs[0]["input"]["document_sha256"] = "0" * 64
    elif mutation == "bound":
        old[0]["input_sha256"] = "0" * 64
    elif mutation == "flag":
        inputs[0]["training_allowed"] = 0
    elif mutation == "wrapper":
        inputs[0]["grade"] = "TS"
    elif mutation == "extra_input":
        inputs[0]["input"]["expected_grade"] = "TS"
    else:
        raw = inputs[0]["input"]
        raw["text"] = raw["text"].replace("가람", "새로운")
        raw["document_sha256"] = text_digest(raw["text"])
        old[0]["input_sha256"] = value_digest(raw)
    with pytest.raises(FactContractError):
        pack.derive(inputs, old)


def make_source(path):
    path.mkdir()
    inputs, old = build_probes()
    data = {"inputs.draft.jsonl": pack._jsonl(inputs), "annotations.draft.jsonl": pack._jsonl(old)}
    for name, text in data.items():
        (path / name).write_text(text, encoding="utf-8", newline="\n")
    manifest = {**FLAGS, "schema_version": "reference-input-fit-pack-v0.2", "files": {k: text_digest(v) for k, v in data.items()}}
    (path / "manifest.json").write_text(pack._json(manifest), encoding="utf-8", newline="\n")
    return path


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("short-fact-test")
    source, out = make_source(root / "source"), root / "output"
    assert pack.build(source, out)["facts"] == 30
    return source, out


def test_pack_roundtrip_immutable_and_usage(built):
    source, out = built
    assert pack.verify(out)["policy_cross_checks"] == 2187
    manifest, data = pack.read_pack(out)
    assert len(data) == 18 and len(manifest["source_files_sha256"]) == 6
    assert manifest["source_pack_manifest_sha256"] == hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest()
    with pytest.raises(FactContractError):
        pack.build(source, out)
    for purpose in ("training", "model_evaluation"):
        with pytest.raises(ValueError):
            assert_dataset_usage([{"text": "anything"}], purpose=purpose, source=out / "prior_hypotheses.jsonl")


@pytest.mark.parametrize("mutation", ["file_hash", "extra_file", "extra_listed", "escape", "empty", "flags", "source_list", "source_hash", "answer", "policy", "summary"])
def test_pack_tamper_fails_even_with_refreshed_hashes(built, tmp_path, mutation):
    out = tmp_path / "tampered"
    shutil.copytree(built[1], out)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    def replace(name, text):
        (out / name).write_text(text, encoding="utf-8", newline="\n")
        manifest["files"][name] = text_digest(text)
    if mutation == "file_hash":
        (out / "summary.json").write_text("{}", encoding="utf-8")
    elif mutation == "extra_file":
        (out / "extra.json").write_text("{}", encoding="utf-8")
    elif mutation == "extra_listed":
        replace("extra.json", "{}")
    elif mutation == "escape":
        manifest["files"]["../escape.json"] = "0" * 64
    elif mutation == "empty":
        manifest["files"] = {}
    elif mutation == "flags":
        manifest["training_allowed"] = 0
    elif mutation == "source_list":
        manifest["source_files_sha256"].pop(pack.SOURCES[0])
    elif mutation == "source_hash":
        manifest["source_files_sha256"][pack.SOURCES[0]] = "0" * 64
    elif mutation == "answer":
        answer = pack.rows((out / "policy_answers.fixed.jsonl").read_bytes())
        answer[0]["answer"]["reference_grade"] = "S3"
        replace("policy_answers.fixed.jsonl", pack._jsonl(answer))
    elif mutation == "policy":
        replace("policy.json", pack._json({**policy.POLICY, "version": "9"}))
    else:
        replace("summary.json", "{}")
    (out / "manifest.json").write_text(pack._json(manifest), encoding="utf-8", newline="\n")
    with pytest.raises((FactContractError, OSError)):
        pack.verify(out)


@pytest.mark.parametrize("data", [b'{"x":1,"x":2}\n', b'{"x":NaN}\n', b'\n', b'not-json\n'])
def test_jsonl_strict(data):
    with pytest.raises(ValueError):
        pack.rows(data)


def test_empty_cli_returns_failure(capsys):
    assert pack.main([]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "invalid"
