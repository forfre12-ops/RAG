"""Preparation contracts and supplied-output arithmetic, NOT customer model scores."""
from __future__ import annotations

import copy
import json
import random
import shutil
import string

import pytest

import prepare_customer_benchmark as cli
from customer_benchmark_drafts import build_drafts
from koipa import customer_benchmark as bench
from koipa.dataset_usage import assert_dataset_usage
from koipa.policy_facts import FactContractError, text_digest, value_digest


def test_document(index, *, family=None):
    # Unnatural random text is only a schema/split fixture, never a manuscript.
    rng = random.Random(4900 + index)
    text = "".join(rng.choices(string.ascii_letters, k=155)) + "\n"
    inp = {"doc_id": "doc-" + text_digest(str(index))[:24], "text": text,
           "context": [{"name": "scope_complete", "value": True, "origin": "synthetic_assumption"}]}
    return {**bench.FLAGS, "schema_version": "customer-synthetic-draft-v1", "document_origin": "synthetic",
        "input": inp, "input_sha256": value_digest(inp), "family_id": f"family-{index if family is None else family}",
        "scenario_id": f"scenario-{index}", "template_family_id": f"template-{index}", "domain": "test-only",
        "claims": [{"name": "first-span", "claim": "Test-only binding", "quote": text[:15], "start": 0, "end": 15,
                    "sha256": text_digest(text[:15]), "status": "authored_binding_only"}]}


test_document.__test__ = False


def answer_for(doc, grade):
    return {"doc_id": doc["input"]["doc_id"], "input_sha256": doc["input_sha256"],
            "policy_id": "test-policy", "policy_version": "0.1", "policy_sha256": "1" * 64,
            "reference_grade": grade, "rule_ids": ["test-rule"], "evidence_names": [doc["claims"][0]["name"]],
            "other_grade_exclusions": {g: "test-only exclusion" for g in bench.GRADES if g != grade}, "status": "authored_candidate"}


def batch(per_grade):
    documents, answers = [], []
    for family in range(per_grade):
        for j, grade in enumerate(bench.GRADES):
            doc = test_document(4 * family + j, family=family)
            documents.append(doc)
            answers.append(answer_for(doc, grade))
    return documents, answers


def predictions_for(documents, answers, profile="body_only"):
    docs = bench.validate_documents(documents)
    by_id = {a["doc_id"]: a for a in answers}
    return [{"doc_id": d.input.doc_id, "input_sha256": d.input_sha256,
             "presented_input_sha256": text_digest(bench.presented_text(d, profile)),
             "policy_sha256": "1" * 64, "model_sha256": "2" * 64, "run_id": "test-run", "profile": profile,
             "status": "ok", "predicted_grade": by_id[d.input.doc_id]["reference_grade"]} for d in docs]


def test_manuscripts_are_twelve_unlabeled_drafts():
    rows = build_drafts()
    docs = bench.validate_documents(rows)
    assert len(docs) == 12 and len({d.domain for d in docs}) == 6
    assert sum(len(d.claims) for d in docs) == 24
    assert all(300 < len(d.input.text) < 1000 for d in docs)
    assert all(any(c.value is None for c in d.input.context) for d in docs)
    assert all("reference_grade" not in r and "label" not in r["input"] for r in rows)
    audit = bench.duplicate_audit(docs)
    assert audit["exact_pairs"] == audit["number_only_pairs"] == audit["near_pairs"] == []
    assert len(audit["groups"]) == 12


@pytest.mark.parametrize("purpose", ["training", "model_evaluation"])
def test_drafts_are_not_training_or_gold(purpose):
    with pytest.raises(ValueError):
        assert_dataset_usage(build_drafts(), purpose=purpose)


@pytest.mark.parametrize("mutation", ["empty", "duplicate", "hash", "span", "quote", "evidence_hash", "claim_duplicate", "context_duplicate",
                                     "extra_input", "real_origin", "human_origin", "status", "flag_true", "flag_integer", "null_text"])
def test_bad_documents_fail(mutation):
    rows = build_drafts()
    r = rows[0]
    if mutation == "empty":
        rows = []
    elif mutation == "duplicate":
        rows[1] = copy.deepcopy(r)
    elif mutation == "hash":
        r["input_sha256"] = "0" * 64
    elif mutation == "span":
        r["claims"][0]["start"] += 1
    elif mutation == "quote":
        r["claims"][0]["quote"] = "unwritten"
    elif mutation == "evidence_hash":
        r["claims"][0]["sha256"] = "0" * 64
    elif mutation == "claim_duplicate":
        r["claims"].append(copy.deepcopy(r["claims"][0]))
    elif mutation == "context_duplicate":
        r["input"]["context"].append(copy.deepcopy(r["input"]["context"][0]))
        r["input_sha256"] = value_digest(r["input"])
    elif mutation == "extra_input":
        r["input"]["label"] = "TS"
    elif mutation == "real_origin":
        r["document_origin"] = "real"
    elif mutation == "human_origin":
        r["input"]["context"][0]["origin"] = "human_review"
    elif mutation == "status":
        r["claims"][0]["status"] = "verified_gold"
    elif mutation == "flag_true":
        r["training_allowed"] = True
    elif mutation == "flag_integer":
        r["training_allowed"] = 0
    else:
        r["input"]["text"] = None
    with pytest.raises(FactContractError):
        bench.validate_documents(rows)


@pytest.mark.parametrize("mutation", ["missing", "duplicate", "input_hash", "policy_id", "policy_version", "policy_hash", "grade", "claim", "claim_duplicate", "exclusion", "signed"])
def test_bad_answers_fail(mutation):
    docs, answers = batch(1)
    a = answers[0]
    if mutation == "missing":
        answers.pop()
    elif mutation == "duplicate":
        answers[1] = copy.deepcopy(a)
    elif mutation in {"input_hash", "policy_hash"}:
        a["input_sha256" if mutation == "input_hash" else "policy_sha256"] = "0" * 64
    elif mutation == "policy_id":
        a["policy_id"] = "different-policy"
    elif mutation == "policy_version":
        a["policy_version"] = "0.2"
    elif mutation == "grade":
        a["reference_grade"] = "S4"
    elif mutation == "claim":
        a["evidence_names"] = ["nonexistent-claim"]
    elif mutation == "claim_duplicate":
        a["evidence_names"] *= 2
    elif mutation == "exclusion":
        a["other_grade_exclusions"]["TS"] = "must not exclude itself"
    else:
        a["status"] = "customer_signed"
    with pytest.raises(FactContractError):
        bench.validate_answers(bench.validate_documents(docs), answers)


def test_normalization_and_transitive_groups():
    assert bench.normalized(" Ａ １２ ") == bench.normalized("a12")
    assert bench.normalized("금액 1,200원", mask_numbers=True) == bench.normalized("금액 3,456원", mask_numbers=True)
    docs, _ = batch(1)
    docs[2]["scenario_id"] = docs[1]["scenario_id"]
    docs[2]["template_family_id"] = docs[3]["template_family_id"]
    audit = bench.duplicate_audit(bench.validate_documents(docs))
    assert len(audit["groups"]) == 1  # All four already share family; links also transitive.


def test_near_duplicates_form_one_connected_group():
    rows = [test_document(i) for i in range(3)]
    for i, r in enumerate(rows):
        text = rows[0]["input"]["text"][:140] + f" 월 합계 {100+i}원. 담당 부서 확인.\n"
        r["input"]["text"] = text
        r["input_sha256"] = value_digest(r["input"])
        r["claims"][0].update(quote=text[:15], sha256=text_digest(text[:15]))
    audit = bench.duplicate_audit(bench.validate_documents(rows))
    assert len(audit["number_only_pairs"]) == 3
    assert len(audit["groups"]) == 1


def test_near_non_numeric_minor_edit_detected():
    rows = [test_document(0), test_document(1)]
    text = rows[0]["input"]["text"].rstrip() + "a\n"
    rows[1]["input"]["text"] = text
    rows[1]["input_sha256"] = value_digest(rows[1]["input"])
    rows[1]["claims"][0].update(quote=text[:15], sha256=text_digest(text[:15]))
    audit = bench.duplicate_audit(bench.validate_documents(rows))
    assert len(audit["near_pairs"]) == 1


def test_known_fixture_cannot_enter_split(monkeypatch):
    docs, answers = batch(3)
    monkeypatch.setattr(bench, "fixture_registry", lambda: {"records": [{"body_fingerprints": [bench.body_fingerprint(docs[0]["input"]["text"])]}]})
    with pytest.raises(FactContractError, match="fixture_blocked"):
        bench.propose_split(docs, answers, train_per_grade=2, evaluation_per_grade=1)


def test_actual_target_size_split_and_permutation_stability():
    docs, answers = batch(250)
    result = bench.propose_split(docs, answers)
    assert result["customer_size_contract_met"] is True
    assert result["train_counts"] == {g: 200 for g in bench.GRADES}
    assert result["evaluation_counts"] == {g: 50 for g in bench.GRADES}
    assert sum(p == "train" for p in result["partitions"].values()) == 800
    assert sum(p == "evaluation" for p in result["partitions"].values()) == 200
    assert len(result["groups"]) == 250
    assert all(len({result["partitions"][i] for i in group}) == 1 for group in result["groups"].values())
    assert result["training_allowed"] is result["model_evaluation_allowed"] is False
    smaller_docs, smaller_answers = batch(3)
    a = bench.propose_split(smaller_docs, smaller_answers, train_per_grade=2, evaluation_per_grade=1)
    b = bench.propose_split(smaller_docs[::-1], smaller_answers[::-1], train_per_grade=2, evaluation_per_grade=1)
    assert a == b


def test_group_quota_impossible_is_failure_not_row_split():
    docs, answers = batch(3)
    for d in docs:
        d["template_family_id"] = "same-template"
    with pytest.raises(FactContractError, match="infeasible_or_timeout"):
        bench.propose_split(docs, answers, train_per_grade=2, evaluation_per_grade=1)


@pytest.mark.parametrize("kwargs", [{"train_per_grade": 0}, {"evaluation_per_grade": True}, {"seed": 1.2}, {}])
def test_wrong_split_quota_fails(kwargs):
    docs, answers = batch(1)
    with pytest.raises(FactContractError):
        bench.propose_split(docs, answers, **kwargs)


def test_exact_duplicate_body_fails_even_if_context_differs():
    docs, answers = batch(3)
    docs[1]["input"]["text"] = docs[0]["input"]["text"]
    docs[1]["claims"] = copy.deepcopy(docs[0]["claims"])
    docs[1]["input_sha256"] = value_digest(docs[1]["input"])
    answers[1]["input_sha256"] = docs[1]["input_sha256"]
    with pytest.raises(FactContractError, match="duplicate_or_fixture"):
        bench.propose_split(docs, answers, train_per_grade=2, evaluation_per_grade=1)


def test_external_pool_scan_reports_missing_coverage_and_never_copies_text(tmp_path):
    docs = bench.validate_documents(build_drafts())
    path = tmp_path / "pool.jsonl"
    path.write_text(json.dumps({"text": docs[0].input.text}) + '\n{"metadata_only":true}\nnot-json\n', encoding="utf-8")
    result = bench.audit_external_pool(docs, [path])
    assert result["text_rows_checked"] == 1
    assert result["rows_without_supported_text"] == 1
    assert len(result["parse_failures"]) == 1 and len(result["matches"]) == 1
    assert result["coverage_complete"] is False
    assert docs[0].input.text not in json.dumps(result, ensure_ascii=False)


def test_empty_pool_scan_is_not_success(tmp_path):
    with pytest.raises(FactContractError, match="pool_empty"):
        bench.audit_external_pool(bench.validate_documents(build_drafts()), [])
    empty = tmp_path / "empty.jsonl"
    empty.write_text("", encoding="utf-8")
    result = bench.audit_external_pool(bench.validate_documents(build_drafts()), [empty])
    assert result["text_rows_checked"] == 0 and result["coverage_complete"] is False


def test_all_correct_two_hundred_only_tests_scorer_arithmetic():
    docs, answers = batch(50)
    result = bench.score_predictions(docs, answers, predictions_for(docs, answers))
    assert result["denominator"] == 200 and result["customer_size_contract_met"]
    assert result["grade_agreement_all_gold"] == result["resolved_grade_agreement_all_gold"] == 1
    assert result["run_complete"] and result["inference_performed_here"] is False
    assert result["customer_accuracy_measured"] is False


def test_hold_error_missing_stay_in_denominator():
    docs, answers = batch(1)
    predictions = predictions_for(docs, answers)
    predictions[0].update(status="needs_review")  # Correct proposal but unresolved.
    predictions[1].update(status="error", predicted_grade=None)
    predictions[2].update(status="needs_review", predicted_grade=None)
    predictions.pop()
    result = bench.score_predictions(docs, answers, predictions, expected_count=4)
    assert result["grade_agreement_all_gold"] == .25
    assert result["resolved_grade_agreement_all_gold"] == 0
    assert result["review_rate_all_gold"] == .5
    assert result["confusion_matrix"]["S1"]["ERROR"] == 1
    assert result["confusion_matrix"]["S2"]["HOLD"] == 1
    assert result["confusion_matrix"]["S3"]["MISSING"] == 1
    assert result["high_grade_unresolved"] == 2 and result["ts_s1_to_s3"] == 0
    assert result["run_complete"] is False


def test_all_missing_not_zero_denominator_or_success():
    docs, answers = batch(1)
    result = bench.score_predictions(docs, answers, [], expected_count=4)
    assert result["denominator"] == 4 and result["grade_agreement_all_gold"] == 0
    assert result["statuses"]["missing"] == 4 and result["run_complete"] is False


def test_directional_confusions_and_s3_overclassification():
    docs, answers = batch(2)
    predictions = predictions_for(docs, answers)
    for p, label in zip(predictions, ("S3", "S2", "S1", "TS", "S3", "S3", "S3", "S3"), strict=True):
        p["predicted_grade"] = label
    result = bench.score_predictions(docs, answers, predictions, expected_count=8)
    assert result["ts_s1_to_s3"] == 3 and result["s2_to_s3"] == 1
    assert result["s1_s2_confusions"] == 2 and result["s3_overclassified"] == 1
    assert result["macro_recall"] == .125


@pytest.mark.parametrize("mutation", ["duplicate", "extra_id", "input_hash", "presented_hash", "policy", "run", "model", "profile", "null_ok", "error_grade", "unexpected_field"])
def test_bad_predictions_fail(mutation):
    docs, answers = batch(1)
    predictions = predictions_for(docs, answers)
    p = predictions[0]
    if mutation == "duplicate":
        predictions.append(copy.deepcopy(p))
    elif mutation == "extra_id":
        p["doc_id"] = "doc-" + "0" * 24
    elif mutation in {"input_hash", "presented_hash", "policy", "model"}:
        field = {"input_hash": "input_sha256", "presented_hash": "presented_input_sha256", "policy": "policy_sha256", "model": "model_sha256"}[mutation]
        p[field] = "0" * 64
    elif mutation == "run":
        p["run_id"] = "different-run"
    elif mutation == "profile":
        p["profile"] = "body_context"
    elif mutation == "null_ok":
        p["predicted_grade"] = None
    elif mutation == "error_grade":
        p["status"] = "error"
    else:
        p["gold"] = "TS"
    with pytest.raises(FactContractError):
        bench.score_predictions(docs, answers, predictions, expected_count=4)


@pytest.mark.parametrize("profile", ["body_only", "body_context"])
def test_input_profiles_never_include_answer_sidecar(profile):
    docs = bench.validate_documents(build_drafts())
    text = bench.presented_text(docs[0], profile)
    assert "reference_grade" not in text and "other_grade_exclusions" not in text
    assert "template-" not in text and "authored_binding_only" not in text
    assert ("[가상 맥락]" in text) == (profile == "body_context")


@pytest.mark.parametrize("data", ['{"x":1,"x":2}', '{"x":NaN}', '{"x":Infinity}', '{"x":1e999}', 'broken'])
def test_strict_json(data):
    with pytest.raises(ValueError):
        bench.strict_loads(data)


@pytest.fixture(scope="module")
def prepared(tmp_path_factory):
    out = tmp_path_factory.mktemp("customer-benchmark-test") / "pack"
    result = cli.prepare(out)
    assert result["authored_documents"] == 12 and result["fixed_grade_answers"] == 0
    return out


def test_preparation_pack_roundtrip_is_not_release(prepared):
    result = cli.verify(prepared)
    assert result["accepted_training_documents"] == result["accepted_evaluation_documents"] == 0
    assert result["target_documents"] == 1000 and result["policy_selection"] is None
    with pytest.raises(FactContractError, match="output_exists"):
        cli.prepare(prepared)
    with pytest.raises(ValueError):
        assert_dataset_usage([{"text": "text"}], purpose="training", source=prepared / "documents")


@pytest.mark.parametrize("mutation", ["hash", "unlisted", "listed_extra", "escape", "flags", "source", "summary", "input", "answer_added"])
def test_pack_tampering_fails(prepared, tmp_path, mutation):
    out = tmp_path / "copy"
    shutil.copytree(prepared, out)
    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    def replace(name, data):
        (out / name).write_text(data, encoding="utf-8", newline="\n")
        manifest["files"][name] = text_digest(data)
    if mutation == "hash":
        (out / "summary.json").write_text("{}", encoding="utf-8")
    elif mutation == "unlisted":
        (out / "extra.json").write_text("{}", encoding="utf-8")
    elif mutation == "listed_extra":
        replace("extra.json", "{}")
    elif mutation == "escape":
        manifest["files"]["../escape.txt"] = "0" * 64
    elif mutation == "flags":
        manifest["training_allowed"] = 0
    elif mutation == "source":
        manifest["source_files_sha256"][cli.SOURCES[0]] = "0" * 64
    elif mutation == "summary":
        replace("summary.json", '{"authored_documents":1000}')
    elif mutation == "input":
        rows = cli._rows((out / "inputs/draft_inputs.jsonl").read_bytes())
        rows[0]["label"] = "TS"
        replace("inputs/draft_inputs.jsonl", cli._jsonl(rows))
    else:
        replace("answers/gold.jsonl", '{"label":"TS"}\n')
    (out / "manifest.json").write_text(cli._json(manifest), encoding="utf-8", newline="\n")
    with pytest.raises((FactContractError, OSError)):
        cli.verify(out)


def test_cli_wrong_small_split_fails_and_creates_no_output(tmp_path, capsys):
    docs, answers = batch(1)
    doc_path, answer_path, out = tmp_path / "docs.jsonl", tmp_path / "answers.jsonl", tmp_path / "split.json"
    doc_path.write_text(cli._jsonl(docs), encoding="utf-8")
    answer_path.write_text(cli._jsonl(answers), encoding="utf-8")
    assert cli.main(["split", "--documents", str(doc_path), "--answers", str(answer_path), "--out", str(out)]) == 2
    assert not out.exists()
    assert json.loads(capsys.readouterr().out)["status"] == "failed"
