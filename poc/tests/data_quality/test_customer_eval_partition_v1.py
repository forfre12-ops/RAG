"""Exposure exclusion and exact quotas on synthetic test fixtures, not manuscripts."""
import copy
import hashlib
import json
import random
import string
from collections import Counter
from types import SimpleNamespace
from pathlib import Path

import pytest

import prepare_customer_eval_partition_v1 as cli
from koipa import customer_eval_partition_v1 as part
from koipa.customer_benchmark import FLAGS, GRADES
from koipa.policy_facts import FactContractError, text_digest, value_digest


def document(index, family=None):
    rng = random.Random(578300 + index)
    text = "".join(rng.choices(string.ascii_letters, k=180)) + f" count {index + 50}."
    inp = {"doc_id": "doc-" + text_digest(str(index))[:24], "text": text, "context": []}
    return {**FLAGS, "schema_version": "customer-synthetic-draft-v1", "document_origin": "synthetic", "input": inp,
            "input_sha256": value_digest(inp), "family_id": f"family-{family if family is not None else index}",
            "scenario_id": f"scenario-{index}", "template_family_id": f"template-{index}", "domain": "test-only",
            "claims": [{"name": "claim-one", "claim": "Test fixture span", "quote": text[:15], "start": 0, "end": 15,
                        "sha256": text_digest(text[:15]), "status": "authored_binding_only"}]}


def answer(d, grade):
    return {"doc_id": d["input"]["doc_id"], "input_sha256": d["input_sha256"], "policy_id": "test-policy", "policy_version": "0.1",
            "policy_sha256": "a" * 64, "reference_grade": grade, "rule_ids": ["test-rule"], "evidence_names": ["claim-one"],
            "other_grade_exclusions": {g: "test-only exclusion" for g in GRADES if g != grade}, "status": "authored_candidate"}


def batch(per_grade=3):
    docs, answers = [], []
    for i in range(per_grade):
        for offset, g in enumerate(GRADES):
            d = document(i * 4 + offset, family=i)
            docs.append(d)
            answers.append(answer(d, g))
    return docs, answers


def ledger(docs):
    return part.build_exposure_ledger(docs, source_ref="test/diagnostic.json", source_sha256="b" * 64,
                                      reason="diagnostic_fit_or_selection")


def audit(docs, history, **kwargs):
    return part.audit_exposure(docs, history, expected_ledger_sha256=history["ledger_sha256"], **kwargs)


def split(docs, answers, history, **kwargs):
    return part.propose_exposure_safe_split(docs, answers, history, expected_ledger_sha256=history["ledger_sha256"],
                                           train_per_grade=2, evaluation_per_grade=1, **kwargs)


def reseal(history):
    history["ledger_sha256"] = value_digest({k: v for k, v in history.items() if k != "ledger_sha256"})


def test_ledger_has_no_body_answer_permission_or_identity_certificate():
    docs, _ = batch()
    history = ledger(docs[:4])
    assert history["record_count"] == 4
    assert all(history[k] is False for k in FLAGS)
    assert not history["complete_access_history_certified"] and not history["blindness_certified"]
    assert docs[0]["input"]["text"] not in json.dumps(history)
    assert "reference_grade" not in json.dumps(history)
    assert part.validate_exposure_ledger(history, expected_ledger_sha256=history["ledger_sha256"]) == history


@pytest.mark.parametrize("mutation", ["empty", "count", "boolcount", "duplicate", "order", "extra", "flag", "intflag", "source", "reason", "id", "hash", "family", "recordextra"])
def test_rehashed_invalid_ledgers_fail(mutation):
    history = ledger(batch()[0][:4])
    row = history["records"][0]
    if mutation == "empty":
        history["records"], history["record_count"] = [], 0
    elif mutation == "count":
        history["record_count"] = 1
    elif mutation == "boolcount":
        history["record_count"] = True
    elif mutation == "duplicate":
        history["records"][1] = copy.deepcopy(row)
    elif mutation == "order":
        history["records"].reverse()
    elif mutation == "extra":
        history["approved"] = True
    elif mutation in {"flag", "intflag"}:
        history["training_allowed"] = True if mutation == "flag" else 0
    elif mutation == "source":
        row["source_ref"] = "  "
    elif mutation == "reason":
        row["reason"] = "not_exposed"
    elif mutation == "id":
        row["doc_id"] = "not-document"
    elif mutation == "hash":
        row["input_sha256"] = "invalid"
    elif mutation == "family":
        row["family_id"] = ""
    else:
        row["approved"] = True
    reseal(history)
    with pytest.raises(FactContractError):
        part.validate_exposure_ledger(history, expected_ledger_sha256=history["ledger_sha256"])


def test_pinned_hash_blocks_rehashed_record_removal():
    docs, _ = batch()
    history = ledger(docs[:4])
    pinned = history["ledger_sha256"]
    history["records"].pop()
    history["record_count"] -= 1
    reseal(history)
    with pytest.raises(FactContractError, match="hash_mismatch"):
        part.audit_exposure(docs, history, expected_ledger_sha256=pinned)


@pytest.mark.parametrize("history,pin", [(None, "1" * 64), ({}, "1" * 64), ({}, None)])
def test_ledger_is_required(history, pin):
    with pytest.raises(FactContractError):
        part.audit_exposure(batch()[0], history, expected_ledger_sha256=pin)


def test_empty_documents_fail():
    with pytest.raises(FactContractError):
        ledger([])


def test_extend_is_append_only_and_no_mutation():
    docs, _ = batch()
    history = ledger(docs[:4])
    before = copy.deepcopy(history)
    extended = part.extend_exposure_ledger(history, docs[4:8], expected_ledger_sha256=history["ledger_sha256"],
                                           source_ref="new/authoring.json", source_sha256="c" * 64, reason="development_authoring")
    assert history == before and extended["record_count"] == 8
    assert all(row in extended["records"] for row in history["records"])
    with pytest.raises(FactContractError, match="overwrite"):
        part.extend_exposure_ledger(history, docs[:1], expected_ledger_sha256=history["ledger_sha256"],
                                    source_ref="new.json", source_sha256="c" * 64, reason="development_authoring")


def test_exposure_spreads_to_whole_declared_family():
    docs, _ = batch()
    history = ledger(docs[:1])
    result = audit(docs, history)
    assert len(result["direct_matches"]) == 4
    assert result["forbidden_evaluation_count"] == 4
    assert result["unmatched_count_not_proof_of_unexposed"] == 8


def test_semantic_links_propagate_transitively_and_bind_hashes():
    docs = [document(i) for i in range(4)]
    ids = [d["input"]["doc_id"] for d in docs]
    links = [{"members": ids[:2], "reason": "same authored semantic event one"},
             {"members": ids[1:3], "reason": "same authored semantic event two"}]
    result = audit(docs, ledger(docs[:1]), semantic_links=links)
    assert result["forbidden_evaluation_ids"] == sorted(ids[:3])
    assert set(result["direct_matches"]) == {ids[0]}
    assert len(result["semantic_links"][0]["input_hashes"]) == 2
    assert result == audit(docs[::-1], ledger(docs[:1]), semantic_links=links[::-1])


@pytest.mark.parametrize("kind", ["missing", "duplicate", "one", "reason", "extra", "doublelink"])
def test_invalid_semantic_links_fail(kind):
    docs = [document(0), document(1)]
    link = {"members": [d["input"]["doc_id"] for d in docs], "reason": "same authored example event"}
    if kind == "missing":
        link["members"][1] = "doc-" + "a" * 24
    elif kind == "duplicate":
        link["members"][1] = link["members"][0]
    elif kind == "one":
        link["members"].pop()
    elif kind == "reason":
        link["reason"] = " "
    elif kind == "extra":
        link["grade"] = "TS"
    links = [link, copy.deepcopy(link)] if kind == "doublelink" else [link]
    with pytest.raises(FactContractError):
        audit(docs, ledger(docs[:1]), semantic_links=links)


@pytest.mark.parametrize("field", part.FAMILIES)
def test_prior_parent_absent_still_blocks_matching_family(field):
    original, current = document(500), document(900)
    current[field] = original[field]
    result = audit([current], ledger([original]))
    assert result["forbidden_evaluation_count"] == 1
    assert result["ledger_records_absent_from_current_inputs"] == [original["input"]["doc_id"]]


@pytest.mark.parametrize("kind", ["exact", "space", "number"])
def test_renamed_rekeyed_copy_blocked_even_when_parent_absent(kind):
    original, current = document(500), document(900)
    body = original["input"]["text"]
    if kind == "space":
        body += " \n"
    elif kind == "number":
        body = body.replace("550", "9999")
    current["input"]["text"] = body
    current["input_sha256"] = value_digest(current["input"])
    current["claims"] = copy.deepcopy(original["claims"])
    result = audit([current], ledger([original]))
    assert result["forbidden_evaluation_count"] == 1
    assert "number_masked_body_sha256" in result["direct_matches"][current["input"]["doc_id"]]


def test_same_id_rewrite_rejected_even_with_updated_input_hash():
    original = document(0)
    current = copy.deepcopy(original)
    current["input"]["text"] += " 변경"
    current["input_sha256"] = value_digest(current["input"])
    with pytest.raises(FactContractError, match="same_id_changed"):
        audit([current], ledger([original]))


def test_group_disjoint_split_excludes_exposed_parents_and_is_stable():
    docs, answers = batch()
    history = ledger(docs[:4])
    result = split(docs, answers, history)
    assert all(result["partitions"][d["input"]["doc_id"]] == "train" for d in docs[:4])
    assert result["train_counts"] == dict.fromkeys(GRADES, 2)
    assert result["evaluation_counts"] == dict.fromkeys(GRADES, 1)
    assert all(result[k] is False for k in FLAGS)
    assert not result["blindness_certified"] and not result["release_authorized"]
    assert result == split(docs[::-1], answers[::-1], history)


def test_full_1000_test_strings_split_not_customer_manuscripts():
    docs, answers = batch(250)
    history = ledger(docs[:164])
    result = part.propose_exposure_safe_split(docs, answers, history, expected_ledger_sha256=history["ledger_sha256"])
    assert result["customer_size_contract_met"]
    assert Counter(result["partitions"].values()) == {"train": 800, "evaluation": 200}
    assert all(result["partitions"][d["input"]["doc_id"]] == "train" for d in docs[:164])


def test_forced_train_over_capacity_fails_before_solver():
    docs, answers = batch()
    with pytest.raises(FactContractError, match="capacity"):
        split(docs, answers, ledger(docs))


def test_infeasible_groups_cannot_be_split_to_meet_count():
    docs, answers = batch()
    for d in docs[4:]:
        d["template_family_id"] = "connected-holdout"
    with pytest.raises(FactContractError, match="infeasible_or_timeout"):
        split(docs, answers, ledger(docs[:4]))


@pytest.mark.parametrize("field,value", [("train_per_grade", True), ("evaluation_per_grade", 0), ("seed", 0.5)])
def test_invalid_split_parameters(field, value):
    docs, answers = batch()
    history = ledger(docs[:4])
    kwargs = {"train_per_grade": 2, "evaluation_per_grade": 1, "seed": 1, field: value}
    with pytest.raises(FactContractError):
        part.propose_exposure_safe_split(docs, answers, history, expected_ledger_sha256=history["ledger_sha256"], **kwargs)


def test_ledger_and_documents_not_mutated():
    docs, answers = batch()
    history = ledger(docs[:4])
    original = copy.deepcopy((docs, answers, history))
    split(docs, answers, history)
    assert (docs, answers, history) == original


def test_solver_timeout_is_not_success(monkeypatch):
    import scipy.optimize
    monkeypatch.setattr(scipy.optimize, "milp", lambda **kwargs: SimpleNamespace(success=False, x=None))
    docs, answers = batch()
    with pytest.raises(FactContractError, match="infeasible_or_timeout"):
        split(docs, answers, ledger(docs[:4]))


def test_known_fixture_and_exact_copies_cannot_enter_split(monkeypatch):
    docs, answers = batch()
    history = ledger(docs[:4])
    original_audit = part.duplicate_audit
    def flagged(rows):
        result = original_audit(rows)
        result["known_fixture_body_matches"] = [rows[-1].input.doc_id]
        return result
    monkeypatch.setattr(part, "duplicate_audit", flagged)
    with pytest.raises(FactContractError, match="fixture_blocked"):
        split(docs, answers, history)


def test_current_small_manuscript_count_is_not_a_complete_split():
    docs, answers = batch()
    history = ledger(docs[:4])
    with pytest.raises(FactContractError, match="grade_quota"):
        part.propose_exposure_safe_split(docs, answers, history, expected_ledger_sha256=history["ledger_sha256"])


def test_solver_cannot_return_forbidden_selection(monkeypatch):
    import numpy as np
    import scipy.optimize
    monkeypatch.setattr(scipy.optimize, "milp", lambda **kwargs: SimpleNamespace(success=True, x=np.ones(len(kwargs["c"]))))
    docs, answers = batch()
    with pytest.raises(FactContractError, match="selected_forbidden"):
        split(docs, answers, ledger(docs[:4]))


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cli_ledger_and_audit_pin_files_and_do_not_overwrite(tmp_path):
    docs, _ = batch()
    path, source = tmp_path / "docs.jsonl", tmp_path / "diagnostic.json"
    path.write_text("\n".join(json.dumps(d) for d in docs), encoding="utf-8")
    source.write_text("{}", encoding="utf-8")
    out = tmp_path / "ledger.json"
    args = ["ledger", "--documents", str(path), "--documents-sha256", file_hash(path), "--source", str(source),
            "--source-sha256", file_hash(source), "--reason", "development_authoring", "--out", str(out)]
    assert cli.main(args) == 0
    initial = out.read_bytes()
    assert cli.main(args) == 2 and out.read_bytes() == initial
    history = json.loads(initial)
    report = tmp_path / "audit.json"
    audit_args = ["audit", "--documents", str(path), "--documents-sha256", file_hash(path), "--ledger", str(out),
                  "--ledger-file-sha256", file_hash(out), "--ledger-sha256", history["ledger_sha256"], "--out", str(report)]
    assert cli.main(audit_args) == 0
    assert json.loads(report.read_text())["forbidden_evaluation_count"] == 12
    changed = tmp_path / "wrong.json"
    audit_args[-1] = str(changed)
    path.write_text(path.read_text() + "\n", encoding="utf-8")
    assert cli.main(audit_args) == 2 and not changed.exists()


def test_cli_zero_documents_fails(tmp_path):
    source = tmp_path / "zero.jsonl"
    source.write_text("", encoding="utf-8")
    out = tmp_path / "out.json"
    assert cli.main(["ledger", "--documents", str(source), "--documents-sha256", file_hash(source), "--source", str(source),
                     "--source-sha256", file_hash(source), "--reason", "development_authoring", "--out", str(out)]) == 2
    assert not out.exists()


def test_cli_cannot_add_unlisted_file_inside_frozen_pack(tmp_path):
    frozen = tmp_path / "frozen"
    inputs = frozen / "authoring"
    inputs.mkdir(parents=True)
    (frozen / "manifest.json").write_text("{}", encoding="utf-8")
    path = inputs / "documents.jsonl"
    path.write_text(json.dumps(document(0)), encoding="utf-8")
    out = frozen / "audit" / "new-exposure.json"
    assert cli.main(["ledger", "--documents", str(path), "--documents-sha256", file_hash(path),
                     "--source", str(path), "--source-sha256", file_hash(path), "--reason", "development_authoring",
                     "--out", str(out)]) == 2
    assert not out.exists()


def test_cli_detects_write_time_input_change(tmp_path, monkeypatch):
    path = tmp_path / "documents.jsonl"
    path.write_text(json.dumps(document(0)), encoding="utf-8")
    pin = file_hash(path)
    original = Path.read_bytes
    out = tmp_path / "result.json"
    def read_during_write(self):
        data = original(self)
        return data + b" " if self.resolve() == path.resolve() and out.exists() else data
    monkeypatch.setattr(Path, "read_bytes", read_during_write)
    assert cli.main(["ledger", "--documents", str(path), "--documents-sha256", pin, "--source", str(path),
                     "--source-sha256", pin, "--reason", "development_authoring", "--out", str(out)]) == 2


def test_cli_same_path_two_roles_preserves_first_snapshot(tmp_path, monkeypatch):
    path = tmp_path / "documents.jsonl"
    raw = json.dumps(document(0)).encode()
    path.write_bytes(raw)
    changed = raw + b" "
    pins = [hashlib.sha256(v).hexdigest() for v in (raw, changed)]
    original = Path.read_bytes
    calls = 0
    def successive_reads(self):
        nonlocal calls
        if self.resolve() == path.resolve():
            calls += 1
            return raw if calls == 1 else changed
        return original(self)
    monkeypatch.setattr(Path, "read_bytes", successive_reads)
    out = tmp_path / "result.json"
    assert cli.main(["ledger", "--documents", str(path), "--documents-sha256", pins[0], "--source", str(path),
                     "--source-sha256", pins[1], "--reason", "development_authoring", "--out", str(out)]) == 2
    assert not out.exists()
