"""Crossed independent manuscripts, explicit premises and fail-closed pack checks."""
from __future__ import annotations

import copy
import json
import re
import shutil
from collections import Counter
from decimal import Decimal

import pytest

import build_customer_guide_batch05 as batch
from customer_guide_batch05 import CASES, build_new_cases
from koipa.customer_guide_reference import decode_context, decide
from koipa.dataset_usage import assert_dataset_usage
from koipa.policy_facts import FactContractError, text_digest, value_digest


@pytest.fixture(scope="module")
def material():
    return batch.core_payload()


@pytest.fixture(scope="module")
def packet(tmp_path_factory):
    root = tmp_path_factory.mktemp("batch05")/"pack"
    batch.prepare(root)
    return root


def test_196_preserve_original_164_records_answers_evidence(material):
    docs, answers, payload = material
    old_docs, old_answers, old_payload = batch.previous.core_payload()
    assert len(docs) == len(answers) == len({d.input.text for d in docs}) == 196
    by_id = {d.input.doc_id: d.model_dump() for d in docs}
    assert all(by_id[d.input.doc_id] == d.model_dump() for d in old_docs)
    by_answer = {a["doc_id"]: a for a in answers}
    assert all(by_answer[a["doc_id"]] == a for a in old_answers)
    by_evidence = {r["doc_id"]: r for r in batch._rows(payload["answers/evidence.jsonl"].encode())}
    assert all(by_evidence[r["doc_id"]] == r for r in batch._rows(old_payload["answers/evidence.jsonl"].encode()))
    assert Counter(a["reference_grade"] for a in answers) == {"TS": 38, "S1": 56, "S2": 49, "S3": 53}
    summary = json.loads(payload["summary.json"])
    assert sum(summary["remaining_before_rejections"].values()) == 804
    assert summary["quote_bound_claims"] == 392 and summary["context_fact_bindings"] == 2744
    assert summary["arithmetic_checks"] == 143 and summary["new_arithmetic_checks"] == 32
    assert summary["accepted_train"] == summary["accepted_evaluation"] == summary["body_only_grade_eligible"] == 0
    assert summary["new_bodies"] == 32 and summary["unchanged_previous_bodies"] == 164
    assert summary["policy_version"] == "0.1"
    assert value_digest(batch.POLICY) == "e3aa2854120397342574c2487a46c83a4b74b9108c706bbc3c4b47c8dfbe1ac9"
    for name, content in old_payload.items():
        if name.startswith("audit/") and "arithmetic" in name:
            assert payload["audit/inherited/"+name.removeprefix("audit/")] == content


def test_cross_32_without_target_assignment_or_metadata_input_leak(material):
    _, _, payload = material
    report = json.loads(payload["audit/cross_design.json"])
    assert report["grade_counts"] == {g: 8 for g in batch.GRADES}
    assert report["domain_register_grade_counts"] == {
        d: {r: {g: 1 for g in batch.GRADES} for r in batch.REGISTERS} for d in batch.DOMAINS}
    assert report["form_grade_counts"] == {f: {g: 2 for g in batch.GRADES} for f in ("memo", "log", "qa", "procedure")}
    assert report["body_style_bias_resolved"] is report["semantic_independence_certified"] is False
    assert len(report["metadata"]) == 32
    assert all(r["final_blind_evaluation_eligible"] is False for r in report["metadata"])
    assert len(CASES) == len({r["key"] for r in CASES}) == 32
    assert not any({"grade", "reference_grade", "label"} & r.keys() for r in CASES)
    inputs = batch._rows(payload["inputs/body_context.jsonl"].encode())
    assert [r["doc_id"] for r in inputs] == sorted(r["doc_id"] for r in inputs)
    forbidden = {"register", "form", "independence_note", "family_id", "reference_grade", "label", "rationale", "S", "V", "M"}
    assert all(not forbidden & r.keys() for r in inputs)
    assert all(200 <= len(r["input"]["text"]) <= 500 for r, _, _ in build_new_cases())
    assert not any(re.search(r"\b(?:TS|S1|S2|S3)\b|guide-s-|trial-v-|guide-m-", r["input"]["text"])
                   for r, _, _ in build_new_cases())


@pytest.mark.parametrize("index", range(32))
def test_every_new_body_calculation_rejects_wrong_result(material, index):
    docs = list(material[0])
    spec = CASES[index]
    pos = next(i for i, d in enumerate(docs) if d.family_id == "family-"+spec["key"])
    original = docs[pos]
    match = re.search(spec["pattern"], original.input.text)
    start, end = match.span(match.lastindex)
    incorrect = str(Decimal(match.group(match.lastindex))+1)
    changed = original.input.text[:start]+incorrect+original.input.text[end:]
    docs[pos] = original.model_copy(update={"input": original.input.model_copy(update={"text": changed})})
    with pytest.raises(FactContractError, match="arithmetic_mismatch"):
        batch.arithmetic(docs)


@pytest.mark.parametrize("index", range(32))
def test_each_document_binds_body_context_and_only_conditional_grade(index):
    record, answer, detail = build_new_cases()[index]
    assert len(record["claims"]) == 2
    for claim in record["claims"]:
        assert record["input"]["text"][claim["start"]:claim["end"]] == claim["quote"] == claim["claim"]
        assert text_digest(claim["quote"]) == claim["sha256"]
    assert len(detail["context_evidence"]) == 14
    assert all(e["origin"] == "synthetic_assumption" for e in detail["context_evidence"])
    assert decode_context(record["input"]["context"]) == detail["premises"]
    assert decide(detail["premises"]) == detail["decision"]
    assert detail["decision"]["reference_grade"] == answer["reference_grade"]
    assert detail["body_only_grade_scoring_allowed"] is False
    assert detail["real_world_premises_verified"] is False
    assert detail["body_only_result"] == "HOLD"
    assert len(answer["other_grade_exclusions"]) == 3
    assert all(record[k] is detail[k] is False for k in batch.FLAGS)


def test_s3_is_not_external_disclosure_authorization():
    staff = [e for _, a, e in build_new_cases() if a["reference_grade"] == "S3" and not e["premises"]["release_authorized"]]
    assert len(staff) == 3
    assert all(e["decision"]["separate_disclosure_review_required"] is True for e in staff)
    assert all(e["decision"]["disclosure_permission_granted_by_classifier"] is False for _, _, e in build_new_cases())


@pytest.mark.parametrize("purpose", ["training", "model_evaluation"])
def test_no_implicit_release(material, purpose):
    with pytest.raises(ValueError):
        assert_dataset_usage([d.model_dump() for d in material[0]], purpose=purpose)


@pytest.mark.parametrize("change", ["empty", "duplicate", "register", "independence", "label", "hold"])
def test_source_fail_closed(change):
    specs = copy.deepcopy(CASES)
    if change == "empty":
        specs = []
    elif change == "duplicate":
        specs[1] = specs[0]
    elif change == "register":
        specs[0]["register"] = "automatic"
    elif change == "independence":
        specs[0]["independence_note"] = ""
    elif change == "label":
        specs[0]["grade"] = "TS"
    else:
        settings = list(specs[0]["setting"])
        settings[1:3] = [1500000, 80]
        specs[0]["setting"] = tuple(settings)
    with pytest.raises(FactContractError):
        build_new_cases(specs)


def test_cross_incorrect_or_missing_metadata_fails():
    new = build_new_cases()
    specs = copy.deepcopy(CASES)
    specs[0]["register"] = "polite"
    with pytest.raises(FactContractError, match="cross_balance_invalid"):
        batch.cross_audit(new, specs)
    with pytest.raises(FactContractError, match="cross_count_invalid"):
        batch.cross_audit(new[:-1])


def test_zero_new_cases_fails(monkeypatch):
    monkeypatch.setattr(batch, "build_new_cases", lambda: [])
    with pytest.raises(FactContractError, match="count_invalid"):
        batch.core_payload()


def test_inherited_semantic_groups_are_not_full_independence_certification(material):
    report = json.loads(material[2]["audit/semantic_family_proposals.json"])
    assert report["original_components"] == 196
    assert report["proposed_components"] == 182
    assert report["proposal_member_documents"] == 18 and len(report["proposals"]) == 4
    assert report["semantic_independence_certified"] is report["split_rule_approved"] is False
    assert report["new_documents"] == 0
    duplicates = json.loads(material[2]["audit/duplicates.json"])
    assert all(not duplicates[k] for k in ("exact_pairs", "number_only_pairs", "near_pairs", "known_fixture_body_matches"))


def test_prepare_verify_nonoverwrite_missing_and_cli_parent_required(packet, tmp_path):
    summary = batch.verify(packet)
    assert summary["unique_bodies"] == 196
    assert len(list((packet/"documents").glob("*.txt"))) == 196
    with pytest.raises(FactContractError, match="output_exists"):
        batch.prepare(packet)
    with pytest.raises(SystemExit) as exc:
        batch.main(["prepare", "--out", str(tmp_path/"new")])
    assert exc.value.code == 2
    assert batch.main(["verify", "--pack", str(tmp_path/"missing")]) == 2
    assert batch.main(["verify", "--pack", str(packet)]) == 0
    manifest = json.loads((packet/"manifest.json").read_text(encoding="utf-8"))
    assert manifest["parent_pack_verified_during_build"] is False


@pytest.mark.parametrize("mutation", ["zero", "grade", "policy", "context", "arithmetic", "metadata", "source", "flags",
                                      "path", "extra", "missing", "token_empty", "token_notrun_claim", "external_empty"])
def test_rehashed_tampering_is_rejected(packet, tmp_path, mutation):
    root = tmp_path/"copy"
    shutil.copytree(packet, root)
    manifest_path = root/"manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = None
    if mutation == "zero":
        path, content = "authoring/documents.jsonl", ""
    elif mutation == "grade":
        path = "answers/answers.candidate.jsonl"
        rows = batch._rows((root/path).read_bytes())
        rows[0]["reference_grade"] = "TS" if rows[0]["reference_grade"] != "TS" else "S3"
        content = batch._jsonl(rows)
    elif mutation == "policy":
        path = "policy.json"
        policy = copy.deepcopy(batch.POLICY)
        policy["formula"]["4"] = "TS"
        content = batch._json(policy)
    elif mutation == "context":
        path = "inputs/body_context.jsonl"
        rows = batch._rows((root/path).read_bytes())
        rows[0]["context"] = []
        content = batch._jsonl(rows)
    elif mutation == "arithmetic":
        path = "audit/arithmetic.json"
        report = json.loads((root/path).read_text(encoding="utf-8"))
        report["new_checks"][0]["calculated"] = "999"
        content = batch._json(report)
    elif mutation == "metadata":
        path = "authoring/batch05_metadata.jsonl"
        rows = batch._rows((root/path).read_bytes())
        rows[0]["final_blind_evaluation_eligible"] = True
        content = batch._jsonl(rows)
    elif mutation == "source":
        manifest["source_files_sha256"] = {}
    elif mutation == "flags":
        manifest["training_allowed"] = 0
    elif mutation == "path":
        manifest["files"]["../escape"] = "0"*64
    elif mutation == "extra":
        (root/"extra.txt").write_text("extra", encoding="utf-8")
    elif mutation == "missing":
        del manifest["files"]["summary.json"]
    elif mutation == "token_empty":
        path, content = "audit/tokenizer.json", batch._json({"status": "measured", "views": []})
    elif mutation == "token_notrun_claim":
        path, content = "audit/tokenizer.json", batch._json({"status": "not_run", "model_inference_performed": True})
    elif mutation == "external_empty":
        path, content = "audit/external_pool.json", batch._json({"files": [], "text_rows_checked": 0, "matches": []})
    if path:
        (root/path).write_text(content, encoding="utf-8", newline="\n")
        manifest["files"][path] = text_digest(content)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        batch.verify(root)


def test_parent_hash_mismatch_before_any_output(tmp_path, monkeypatch):
    parent = tmp_path/"parent"
    parent.mkdir()
    (parent/"manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(batch.previous, "verify", lambda _: {})
    with pytest.raises(FactContractError, match="parent_pack_mismatch"):
        batch.prepare(tmp_path/"new", parent_pack=parent)
    assert not (tmp_path/"new").exists()


def test_oversize_before_external_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "token_audit", lambda *_: {"status": "measured", "views": [{"fits_512_tokens": False}]})
    with pytest.raises(FactContractError, match="token_budget_exceeded"):
        batch.prepare(tmp_path/"new", tokenizer=tmp_path/"missing", corpus_root=tmp_path/"absent")
    assert not (tmp_path/"new").exists()


@pytest.mark.parametrize("kind", ["empty", "unsupported", "duplicate"])
def test_external_pool_empty_or_match_fails(tmp_path, kind):
    corpus = tmp_path/"corpus"
    corpus.mkdir()
    if kind == "unsupported":
        (corpus/"rows.jsonl").write_text('{"nested":{"text":"unknown"}}\n', encoding="utf-8")
    elif kind == "duplicate":
        text = build_new_cases()[0][0]["input"]["text"]
        (corpus/"rows.jsonl").write_text(json.dumps({"text": text})+"\n", encoding="utf-8")
    with pytest.raises(FactContractError):
        batch.prepare(tmp_path/"new", corpus_root=corpus)
    assert not (tmp_path/"new").exists()
