"""Second authoring batch checks; these tests are not customer inference scores."""
from __future__ import annotations

import copy
import json
from collections import Counter

import pytest

import build_customer_guide_batch02 as batch
from customer_guide_batch02 import CASES, build_new_cases
from koipa.dataset_usage import assert_dataset_usage
from koipa.policy_facts import FactContractError, text_digest, value_digest


def test_eighty_preserve_twenty_and_same_policy():
    docs, answers, payload = batch.core_payload()
    old_docs, old_payload = batch.previous.core_payload()
    assert len(docs) == len(answers) == 80
    assert len({d.input.text for d in docs}) == 80
    by_id = {d.input.doc_id: d.model_dump() for d in docs}
    old_answers = batch._rows(old_payload["answers/answers.candidate.jsonl"].encode())
    answer_map = {a["doc_id"]: a for a in answers}
    detail_map = {e["doc_id"]: e for e in batch._rows(payload["answers/evidence.jsonl"].encode())}
    for d in old_docs:
        assert by_id[d.input.doc_id] == d.model_dump()
    for a in old_answers:
        assert answer_map[a["doc_id"]] == a
    for e in batch._rows(old_payload["answers/evidence.jsonl"].encode()):
        assert detail_map[e["doc_id"]] == e
    assert Counter(a["reference_grade"] for a in answers) == {"TS": 9, "S1": 27, "S2": 20, "S3": 24}
    assert {a["policy_sha256"] for a in answers} == {batch.POLICY_SHA256}
    assert sum(len(d.claims) for d in docs) == 160
    summary = json.loads(payload["summary.json"])
    assert sum(summary["remaining_before_rejections"].values()) == 920
    assert summary["accepted_train"] == summary["accepted_evaluation"] == 0


def test_sources_are_authored_cases_not_quota_expansion():
    assert len(CASES) == len({r["key"] for r in CASES}) == 60
    assert len({r["domain"] for r in CASES}) == 15
    assert all("grade" not in r and "label" not in r for r in CASES)
    data = build_new_cases()
    assert all(200 <= len(d["input"]["text"]) < 1000 for d, _, _ in data)
    assert all("«" not in d["input"]["text"] and "»" not in d["input"]["text"] for d, _, _ in data)
    assert len({d["input"]["doc_id"] for d, _, _ in data}) == 60


def test_inputs_exclude_answers_and_order_does_not_follow_grade_blocks():
    docs, _, payload = batch.core_payload()
    inp = batch._rows(payload["inputs/body_context.jsonl"].encode())
    assert [r["doc_id"] for r in inp] == sorted(r["doc_id"] for r in inp)
    for r, d in zip(inp, docs, strict=True):
        assert r["text"] == d.input.text
        assert not ({"label", "grade", "reference_grade", "rationale", "family_id", "form", "S", "V", "M"} & set(r))
        assert r["training_allowed"] is False
    assert len(inp) == 80


@pytest.mark.parametrize("purpose", ["training", "model_evaluation"])
def test_no_implicit_release(purpose):
    with pytest.raises(ValueError):
        assert_dataset_usage([d.model_dump() for d in batch.core_payload()[0]], purpose=purpose)


def test_form_association_is_not_declared_solved():
    result = batch.form_audit(build_new_cases())
    assert result["n"] == 60
    assert result["in_sample_majority_hits"] == 34
    assert result["in_sample_format_majority"] == 34/60
    # Technical table-style material now spans every grade, not only TS/S1.
    assert set(result["format_grade_counts"]["table"]) == {"TS", "S1", "S2", "S3"}
    assert len(result["format_grade_counts"]["email"]) == 3
    assert "NOT held-out" in result["interpretation"]


@pytest.mark.parametrize("key,expected", [("adhesion-window", "S3"), ("query-cardinality", "S3"),
    ("thermal-storage", "S3"), ("failure-spectrum", "S2"), ("index-layout", "S2"),
    ("channel-mix", "S1"), ("service-interval", "S1"), ("margin-sensitivity", "TS")])
def test_context_diversification_does_not_override_policy(key, expected):
    row = next((d, a, e) for d, a, e in build_new_cases() if d["family_id"] == "family-"+key)
    assert row[1]["reference_grade"] == expected
    assert batch.decide(row[2]["premises"])["reference_grade"] == expected


@pytest.mark.parametrize("index", range(len(batch.CHECKS)))
def test_each_arithmetic_assertion_rejects_changed_stated_value(index):
    docs = batch.core_payload()[0]
    report = batch.arithmetic(docs)
    claim = report["new_checks"][index]
    target_index = next(i for i, d in enumerate(docs) if d.input.doc_id == claim["doc_id"])
    d = docs[target_index]
    # Pattern's last group is always the stated result. Change it, not inputs.
    match = batch.re.search(batch.CHECKS[index][1], d.input.text, batch.re.S)
    start, end = match.span(match.lastindex)
    changed = d.input.text[:start] + str(batch.Decimal(match.group(match.lastindex))+1) + d.input.text[end:]
    docs[target_index] = d.model_copy(update={"input": d.input.model_copy(update={"text": changed})})
    with pytest.raises(FactContractError, match="arithmetic_mismatch"):
        batch.arithmetic(docs)


def test_hold_diagnostics_not_counted_as_documents():
    _, _, payload = batch.core_payload()
    hold = json.loads(payload["audit/hold_probes.json"])
    assert len(hold["cases"]) == 4 and hold["new_document_count"] == 0
    assert all(c["decision"]["status"] == "HOLD" for c in hold["cases"])
    counter = json.loads(payload["audit/body_only_counterexamples.json"])
    assert counter["changed_grade_same_body"] == 80 and counter["new_documents"] == 0


def test_prepare_verify_and_nonoverwrite(tmp_path):
    out = tmp_path/"new"
    result = batch.prepare(out)
    assert result["unique_bodies"] == 80 and result["policy_version"] == "0.1"
    assert batch.verify(out) == result
    assert len(list((out/"documents").glob("*.txt"))) == 80
    assert batch.main(["prepare", "--out", str(out)]) == 2
    assert batch.main(["verify", "--pack", str(tmp_path/"missing")]) == 2


@pytest.mark.parametrize("mutation", ["zero", "missing", "extra", "grade_rehashed", "policy_rehashed", "source", "flags", "path", "token_input"])
def test_tampered_pack_fails(tmp_path, mutation):
    root = tmp_path/"pack"
    batch.prepare(root)
    manifest_file = root/"manifest.json"
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if mutation == "zero":
        path, text = "authoring/documents.jsonl", ""
    elif mutation == "grade_rehashed":
        path = "answers/answers.candidate.jsonl"
        rows = batch._rows((root/path).read_bytes())
        rows[0]["reference_grade"] = "TS" if rows[0]["reference_grade"] != "TS" else "S3"
        text = batch._jsonl(rows)
    elif mutation == "policy_rehashed":
        path = "policy.json"
        policy = copy.deepcopy(batch.POLICY)
        policy["formula"]["4"] = "TS"
        text = batch._json(policy)
    elif mutation == "token_input":
        path = "audit/tokenizer.json"
        text = batch._json({"status": "measured", "views": []})
    else:
        path = None
        if mutation == "missing":
            del manifest["files"]["summary.json"]
        elif mutation == "extra":
            (root/"extra.txt").write_text("probe", encoding="utf-8")
        elif mutation == "source":
            manifest["source_files_sha256"] = {}
        elif mutation == "flags":
            manifest["training_allowed"] = 0
        elif mutation == "path":
            manifest["files"]["../escape"] = "0"*64
    if path:
        (root/path).write_text(text, encoding="utf-8", newline="\n")
        manifest["files"][path] = text_digest(text)
    manifest_file.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises((FactContractError, OSError)):
        batch.verify(root)


def test_zero_source_cases_fail(monkeypatch):
    monkeypatch.setattr(batch, "build_new_cases", lambda: [])
    with pytest.raises(FactContractError):
        batch.core_payload()


def test_audit_requires_supported_pack_and_outside_new_output(tmp_path, monkeypatch):
    assert batch.main(["audit", "--pack", str(tmp_path), "--out", str(tmp_path/"report.json")]) == 2
    root = tmp_path/"pack"
    batch.prepare(root)
    monkeypatch.setattr(batch, "measure", lambda rows, seeds: {"stratified_cv": {"excess_pp": 30}, "family_cv": {"excess_pp": 30}})
    result = batch.audit(root, tmp_path/"report.json")
    assert result["n"] == 80 and set(result["profiles"]) == {"body_only", "context_only", "body_context", "title_only"}
    assert result["training_allowed"] is result["customer_accuracy_measured"] is False
    assert all(v["warning_excess_over_permutation"] for v in result["profiles"].values())
    assert batch.main(["audit", "--pack", str(root), "--out", str(tmp_path/"report.json")]) == 2
    assert batch.main(["audit", "--pack", str(root), "--out", str(root/"report.json")]) == 2


def test_wrong_parent_rejected(tmp_path, monkeypatch):
    parent = tmp_path/"parent"
    parent.mkdir()
    (parent/"manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(batch.previous, "verify", lambda p: {})
    with pytest.raises(FactContractError, match="parent_pack_mismatch"):
        batch.prepare(tmp_path/"out", parent_pack=parent)


def test_oversized_token_view_not_silently_truncated(tmp_path, monkeypatch):
    monkeypatch.setattr(batch, "token_audit", lambda docs, path: {"status": "measured", "views": [{"fits_512_tokens": False}]})
    with pytest.raises(FactContractError, match="token_budget_exceeded"):
        batch.prepare(tmp_path/"out", tokenizer=tmp_path/"tokenizer.json")
    assert not (tmp_path/"out").exists()


def test_policy_digest_remains_fixed():
    assert value_digest(batch.POLICY) == "e3aa2854120397342574c2487a46c83a4b74b9108c706bbc3c4b47c8dfbe1ac9"
