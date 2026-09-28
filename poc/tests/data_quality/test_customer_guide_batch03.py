"""Authoring, evidence and fail-closed regression checks; no model accuracy claim."""
from __future__ import annotations

import copy
import json
from collections import Counter

import pytest

import build_customer_guide_batch03 as batch
from customer_guide_batch03 import CASES, build_new_cases
from koipa.customer_guide_reference import decide
from koipa.dataset_usage import assert_dataset_usage
from koipa.policy_facts import FactContractError, text_digest, value_digest


def test_116_preserve_all_80_records_answers_and_evidence():
    docs, answers, payload = batch.core_payload()
    old_docs, old_answers, old_payload = batch.previous.core_payload()
    assert len(docs) == len(answers) == len({d.input.text for d in docs}) == 116
    by_id = {d.input.doc_id: d.model_dump() for d in docs}
    assert all(by_id[d.input.doc_id] == d.model_dump() for d in old_docs)
    by_answer = {a["doc_id"]: a for a in answers}
    assert all(by_answer[a["doc_id"]] == a for a in old_answers)
    by_detail = {r["doc_id"]: r for r in batch._rows(payload["answers/evidence.jsonl"].encode())}
    assert all(by_detail[r["doc_id"]] == r for r in batch._rows(old_payload["answers/evidence.jsonl"].encode()))
    assert Counter(a["reference_grade"] for a in answers) == {"TS": 18, "S1": 36, "S2": 29, "S3": 33}
    summary = json.loads(payload["summary.json"])
    assert sum(summary["remaining_before_rejections"].values()) == 884
    assert summary["quote_bound_claims"] == 232 and summary["context_fact_bindings"] == 1624
    assert summary["accepted_train"] == summary["accepted_evaluation"] == summary["body_only_grade_eligible"] == 0
    assert summary["arithmetic_checks"] == 63
    assert summary["policy_version"] == "0.1"
    assert value_digest(batch.POLICY) == "e3aa2854120397342574c2487a46c83a4b74b9108c706bbc3c4b47c8dfbe1ac9"


def test_new_manuscripts_have_no_label_argument_or_grade_order_in_input():
    assert len(CASES) == len({r["key"] for r in CASES}) == 36
    assert all(not ({"grade", "label", "reference_grade"} & set(r)) for r in CASES)
    new = build_new_cases()
    assert all(200 <= len(d["input"]["text"]) < 1000 for d, _, _ in new)
    assert all("«" not in d["input"]["text"] and "»" not in d["input"]["text"] for d, _, _ in new)
    _, _, payload = batch.core_payload()
    inputs = batch._rows(payload["inputs/body_context.jsonl"].encode())
    assert [r["doc_id"] for r in inputs] == sorted(r["doc_id"] for r in inputs)
    for row in inputs:
        assert not ({"S", "V", "M", "reference_grade", "label", "grade", "rationale", "form", "family_id"} & set(row))
        assert row["training_allowed"] is False


def test_three_real_formats_each_span_four_grades_but_not_certification():
    report = batch.format_audit(build_new_cases())
    assert report["new_genre_marker_checks"] == 36
    assert report["new_batch"]["format_grade_counts"] == {f: {g: 3 for g in batch.GRADES} for f in ("email", "qa", "log")}
    assert report["new_batch"]["in_sample_format_majority"] == report["new_batch"]["majority_baseline"] == .25
    assert report["annotated_cumulative"]["n"] == 96 and report["cumulative_unannotated_documents"] == 20
    assert report["annotated_cumulative"]["in_sample_majority_hits"] == 43
    assert report["format_bias_resolved"] is report["controlled_before_after_comparison"] is False


def test_false_genre_metadata_does_not_pass_marker_check():
    new = build_new_cases()
    new[0][0]["input"]["text"] = "말머리 없는 표본 본문"
    with pytest.raises(FactContractError, match="genre_marker_missing"):
        batch.format_audit(new)


@pytest.mark.parametrize("index", range(36))
def test_every_stated_arithmetic_result_rejects_mutation(index):
    docs = batch.core_payload()[0]
    spec = CASES[index]
    position = next(i for i, d in enumerate(docs) if d.family_id == "family-"+spec["key"])
    d = docs[position]
    match = batch.re.search(spec["pattern"], d.input.text, batch.re.S)
    start, end = match.span(match.lastindex)
    changed = d.input.text[:start]+str(batch.Decimal(match.group(match.lastindex))+1)+d.input.text[end:]
    docs[position] = d.model_copy(update={"input": d.input.model_copy(update={"text": changed})})
    with pytest.raises(FactContractError, match="arithmetic_mismatch"):
        batch.arithmetic(docs)


@pytest.mark.parametrize("key,grade", [("coolant-poster", "S3"), ("public-kiln", "S3"), ("open-fan-map", "S3"),
    ("staff-survey", "S3"), ("desk-allocation", "S3"), ("office-move", "S3"),
    ("renewal-interview", "TS"), ("channel-interview", "TS"), ("stockout-panel", "TS"),
    ("batch-resume", "S1"), ("envelope-sort", "S2")])
def test_mixed_subjects_costs_and_access_do_not_override_policy(key, grade):
    _, answer, detail = next(r for r in build_new_cases() if r[0]["family_id"] == "family-"+key)
    assert answer["reference_grade"] == grade
    assert decide(detail["premises"])["reference_grade"] == grade
    if key in ("staff-survey", "desk-allocation", "office-move"):
        assert detail["premises"]["release_authorized"] is False


def test_diagnostics_are_not_additional_documents():
    _, _, payload = batch.core_payload()
    probe = json.loads(payload["audit/hold_probes.json"])
    counter = json.loads(payload["audit/body_only_counterexamples.json"])
    assert len(probe["cases"]) == 4 and probe["new_document_count"] == 0
    assert all(r["decision"]["status"] == "HOLD" for r in probe["cases"])
    assert counter["changed_grade_same_body"] == 116 and counter["new_documents"] == 0


@pytest.mark.parametrize("purpose", ["training", "model_evaluation"])
def test_no_implicit_release(purpose):
    with pytest.raises(ValueError):
        assert_dataset_usage([d.model_dump() for d in batch.core_payload()[0]], purpose=purpose)


def test_empty_and_duplicate_source_keys_fail():
    with pytest.raises(FactContractError):
        build_new_cases([])
    with pytest.raises(FactContractError):
        build_new_cases([CASES[0], CASES[0]])


def test_zero_case_build_fails(monkeypatch):
    monkeypatch.setattr(batch, "build_new_cases", lambda: [])
    with pytest.raises(FactContractError):
        batch.core_payload()


def test_prepare_verify_nonoverwrite_and_missing_pack(tmp_path):
    root = tmp_path/"pack"
    result = batch.prepare(root)
    assert batch.verify(root) == result and result["unique_bodies"] == 116
    assert len(list((root/"documents").glob("*.txt"))) == 116
    assert batch.main(["prepare", "--out", str(root)]) == 2
    assert batch.main(["verify", "--pack", str(tmp_path/"missing")]) == 2


@pytest.mark.parametrize("mutation", ["zero", "grade_rehashed", "policy_rehashed", "source", "flags", "path", "extra", "missing", "token_input"])
def test_rehashed_tampering_and_invalid_pack_fail(tmp_path, mutation):
    root = tmp_path/"pack"
    batch.prepare(root)
    manifest_path = root/"manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = None
    if mutation == "zero":
        path, content = "authoring/documents.jsonl", ""
    elif mutation == "grade_rehashed":
        path = "answers/answers.candidate.jsonl"
        rows = batch._rows((root/path).read_bytes())
        rows[0]["reference_grade"] = "TS" if rows[0]["reference_grade"] != "TS" else "S3"
        content = batch._jsonl(rows)
    elif mutation == "policy_rehashed":
        path = "policy.json"
        policy = copy.deepcopy(batch.POLICY)
        policy["formula"]["4"] = "TS"
        content = batch._json(policy)
    elif mutation == "token_input":
        path, content = "audit/tokenizer.json", batch._json({"status": "measured", "views": []})
    elif mutation == "source":
        manifest["source_files_sha256"] = {}
    elif mutation == "flags":
        manifest["training_allowed"] = 0
    elif mutation == "path":
        manifest["files"]["../escape"] = "0"*64
    elif mutation == "extra":
        (root/"extra.txt").write_text("probe", encoding="utf-8")
    elif mutation == "missing":
        del manifest["files"]["summary.json"]
    if path:
        (root/path).write_text(content, encoding="utf-8", newline="\n")
        manifest["files"][path] = text_digest(content)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        batch.verify(root)


def test_parent_hash_mismatch_fails_before_writing(tmp_path, monkeypatch):
    parent = tmp_path/"parent"
    parent.mkdir()
    (parent/"manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(batch.previous, "verify", lambda p: {})
    with pytest.raises(FactContractError, match="parent_pack_mismatch"):
        batch.prepare(tmp_path/"out", parent_pack=parent)
    assert not (tmp_path/"out").exists()


def test_oversize_fails_before_corpus_scan(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "token_audit", lambda docs, path: {"status": "measured", "views": [{"fits_512_tokens": False}]})
    with pytest.raises(FactContractError, match="token_budget_exceeded"):
        batch.prepare(tmp_path/"out", tokenizer=tmp_path/"tokenizer.json", corpus_root=tmp_path/"missing")
    assert not (tmp_path/"out").exists()


@pytest.mark.parametrize("corpus", ["empty", "unsupported", "duplicate"])
def test_empty_or_duplicate_corpus_blocks_pack(tmp_path, corpus):
    root = tmp_path/"corpus"
    root.mkdir()
    if corpus == "unsupported":
        (root/"rows.jsonl").write_text('{"nested": {"text": "unknown"}}\n', encoding="utf-8")
    elif corpus == "duplicate":
        text = build_new_cases()[0][0]["input"]["text"]
        (root/"rows.jsonl").write_text(json.dumps({"text": text}), encoding="utf-8")
    with pytest.raises(FactContractError):
        batch.prepare(tmp_path/"out", corpus_root=root)
    assert not (tmp_path/"out").exists()


def test_audit_profiles_and_output_guards(tmp_path, monkeypatch):
    root = tmp_path/"pack"
    batch.prepare(root)
    monkeypatch.setattr(batch, "measure", lambda rows, seeds: {"stratified_cv": {"excess_pp": 30}, "family_cv": {"excess_pp": 30}})
    report = batch.audit(root, tmp_path/"diagnostic.json")
    assert report["n"] == 116 and set(report["profiles"]) == {"body_only", "context_only", "body_context", "title_only"}
    assert all(v["warning_excess_over_permutation"] for v in report["profiles"].values())
    assert report["customer_accuracy_measured"] is report["training_allowed"] is False
    assert batch.main(["audit", "--pack", str(root), "--out", str(root/"x.json")]) == 2
    assert batch.main(["audit", "--pack", str(root), "--out", str(tmp_path/"diagnostic.json")]) == 2
    assert batch.main(["audit", "--pack", str(root), "--out", str(tmp_path/"new.json"), "--seeds", "0"]) == 2
