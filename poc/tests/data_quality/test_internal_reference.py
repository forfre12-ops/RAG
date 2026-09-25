"""Finite-language answer proofs; not tests of natural-language classification."""
from __future__ import annotations

import copy
import json
from collections import Counter

import pytest

import build_internal_reference as builder
import audit_internal_reference as auditor
import koipa.internal_reference as reference
from koipa.dataset_usage import assert_dataset_usage, assert_path_usage
from koipa.policy_facts import FactContractError, text_digest


def sample(n=2, released=False, size=4):
    return builder.make_input(family=0, linked_people=n, released=released, row_count=size)


def replace_text(raw, text):
    return {**raw, "text": text, "document_sha256": text_digest(text)}


@pytest.mark.parametrize("n,release,expected", [
    (0, False, "S2"), (0, True, "S3"), (0, None, None),
    (1, False, "S1"), (1, True, "S1"), (1, None, "S1"),
    (999, False, "S1"), (999, True, "S1"), (999, None, "S1"),
    (1000, False, "TS"), (1000, True, "TS"), (1000, None, "TS"),
    (1001, False, "TS"), (1001, True, "TS"), (1001, None, "TS"),
])
def test_boundaries(n, release, expected):
    raw = sample(n, release, max(n, 4))
    cert = reference.certify_reference(raw)
    assert cert["reference_grade"] == expected
    assert cert["facts"]["linked_unique_people"] == n
    assert cert["independent_sql_agrees"]
    assert len(cert["joined_evidence"]) == n
    assert reference.verify_certificate(raw, cert)
    for witness in cert["joined_evidence"]:
        for name in ("directory", "payment"):
            span = witness[name]
            assert text_digest(raw["text"][span["start"]:span["end"]]) == span["sha256"]


@pytest.mark.parametrize("context", [None,
    {"origin": "synthetic_assumption", "scope_complete": None, "identity_scope": "supplied_directory_only", "release_authorized": False},
    {"origin": "synthetic_assumption", "scope_complete": False, "identity_scope": "supplied_directory_only", "release_authorized": False},
    {"origin": "synthetic_assumption", "scope_complete": True, "identity_scope": "unknown", "release_authorized": False},
])
@pytest.mark.parametrize("n", [0, 2, 1000])
def test_missing_scope_is_never_fixed(context, n):
    raw = sample(n, size=max(n, 4))
    raw["context"] = context
    cert = reference.certify_reference(raw)
    assert cert["status"] == "hold" and cert["reference_grade"] is None
    assert cert["reasons"] == ["closed_world_context_required"]


@pytest.mark.parametrize("field,value", [("scope_complete", "true"), ("scope_complete", 1),
    ("release_authorized", 0), ("release_authorized", "false"), ("origin", "human_review"),
    ("identity_scope", "everything_is_safe")])
def test_strict_context(field, value):
    raw = sample()
    raw["context"][field] = value
    with pytest.raises(FactContractError):
        reference.certify_reference(raw)


@pytest.mark.parametrize("field,value", [("label", "TS"), ("generation_target", "TS"),
    ("facts", {"linked_unique_people": 1000}), ("policy_sha256", "0" * 64),
    ("document_sha256", "0" * 64), ("doc_id", "")])
def test_reject_answers_extra_fields_and_wrong_hashes(field, value):
    raw = sample()
    raw[field] = value
    with pytest.raises(FactContractError):
        reference.certify_reference(raw)


@pytest.mark.parametrize("change", [
    lambda t: t + "누락되면 안 되는 다른 정보\n",
    lambda t: t.replace("기록형식", "대외비"),
    lambda t: t.replace("[끝]", "[기타]\n추가 정보\n[끝]"),
    lambda t: t.replace("\n", "\r\n"),
    lambda t: t.rstrip("\n"),
    lambda t: t.replace("가상인물-", "실명-", 1),
    lambda t: t.replace("[지급표]", "[연결표]"),
    lambda t: t.replace("금액단위", "금액단위|등급"),
])
def test_whole_document_grammar_rejects_ignored_or_unknown_content(change):
    raw = sample()
    with pytest.raises(FactContractError):
        reference.certify_reference(replace_text(raw, change(raw["text"])))


def test_distinct_people_not_keys_or_payments():
    raw = sample(4)
    tables, _ = reference.parse_document(raw["text"])
    person = tables["[연결표]"][0]
    for row in tables["[연결표]"]:
        row["인물ID"], row["표시명"] = person["인물ID"], person["표시명"]
    cert = reference.certify_reference(replace_text(raw, builder.render(tables, 0)))
    assert cert["facts"]["linked_unique_people"] == 1
    assert len(cert["joined_evidence"]) == 1


@pytest.mark.parametrize("section,key", [("[연결표]", "연결키"), ("[지급표]", "처리키"), ("[배정표]", "작업키")])
def test_duplicate_record_rejected(section, key):
    raw = sample()
    tables, _ = reference.parse_document(raw["text"])
    tables[section][1][key] = tables[section][0][key]
    with pytest.raises(FactContractError, match="duplicate_record"):
        reference.certify_reference(replace_text(raw, builder.render(tables, 0)))


def test_conflicting_person_names_rejected():
    raw = sample()
    tables, _ = reference.parse_document(raw["text"])
    tables["[연결표]"][1]["인물ID"] = tables["[연결표]"][0]["인물ID"]
    with pytest.raises(FactContractError, match="identity_conflict"):
        reference.certify_reference(replace_text(raw, builder.render(tables, 0)))


@pytest.mark.parametrize("family", range(10))
def test_order_and_layout_do_not_change_semantic_answer(family):
    raw = sample()
    tables, _ = reference.parse_document(raw["text"])
    for rows in tables.values():
        rows.reverse()
    result = reference.certify_reference(replace_text(raw, builder.render(tables, family)))
    assert result["reference_grade"] == "S1" and result["facts"]["linked_unique_people"] == 2


def test_empty_identity_and_payment_is_defined_not_missing_scope():
    raw = sample(0, True)
    tables, _ = reference.parse_document(raw["text"])
    tables["[연결표]"] = tables["[지급표]"] = []
    assert reference.certify_reference(replace_text(raw, builder.render(tables, 0)))["reference_grade"] == "S3"


def test_oracle_disagreement_is_failure(monkeypatch):
    monkeypatch.setattr(reference, "sql_oracle", lambda *a, **k: {"linked_unique_people": 0, "possible_grades": ["S3"]})
    with pytest.raises(FactContractError, match="oracle_disagreement"):
        reference.certify_reference(sample())


def test_runtime_policy_drift_is_failure(monkeypatch):
    monkeypatch.setitem(reference.POLICY, "bulk_person_threshold", 2000)
    with pytest.raises(FactContractError, match="runtime_drift"):
        reference.certify_reference(sample())


@pytest.mark.parametrize("field,value", [("reference_grade", "TS"), ("policy_sha256", "0" * 64),
    ("joined_evidence", []), ("excluded_grades", {}), ("gold_eligible", True),
    ("human_signoff_created", True), ("natural_language_semantics_proven", True),
    ("independent_sql_agrees", 1), ("training_allowed", 0)])
def test_certificate_tampering_fails(field, value):
    raw = sample()
    cert = reference.certify_reference(raw)
    cert[field] = value
    with pytest.raises(FactContractError, match="certificate_mismatch"):
        reference.verify_certificate(raw, cert)


def test_s2_s3_body_only_is_not_a_four_grade_truth():
    a, b = sample(0, False), sample(0, True)
    assert a["text"] == b["text"]
    assert reference.certify_reference(a)["reference_grade"] == "S2"
    assert reference.certify_reference(b)["reference_grade"] == "S3"
    a["context"] = None
    assert reference.certify_reference(a)["reference_grade"] is None


@pytest.fixture(scope="module")
def pack(tmp_path_factory):
    path = tmp_path_factory.mktemp("internal-reference") / "pilot"
    result = builder.build_pack(path)
    return path, result


def read_rows(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_complete_pilot_and_replay(pack):
    path, result = pack
    assert result["counts"] == dict.fromkeys(reference.GRADES, 10)
    assert result["unique_bodies"] == 30 and result["unique_text_context_inputs"] == 40
    assert result["partitions"] == {"development": 28, "reserve_candidate": 12}
    assert len(result["intentional_within_family_counterfactual_duplicates"]) == 10
    assert len(result["policy_boundary_checks"]) == 15
    assert builder.verify_pack(path) == result
    assert builder.main(["--verify", str(path)]) == 0
    assert builder.main(["--build", str(path)]) == 2


def test_no_grade_in_inputs_and_style_balanced(pack):
    path, _ = pack
    rows = read_rows(path / "inputs.jsonl")
    answers = {r["certificate"]["doc_id"]: r for r in read_rows(path / "answers.jsonl")}
    groups = {}
    for row in rows:
        raw = row["input"]
        assert set(raw) == {"schema_version", "doc_id", "policy_sha256", "document_sha256", "text", "context"}
        key = reference.style_view(raw)
        groups.setdefault(key, []).append(answers[raw["doc_id"]]["certificate"]["reference_grade"])
    assert len(groups) == 10
    assert all(Counter(g) == dict.fromkeys(reference.GRADES, 1) for g in groups.values())


@pytest.mark.parametrize("purpose", ["training", "model_evaluation"])
def test_existing_usage_guard_denies_new_pack(pack, purpose):
    path, _ = pack
    with pytest.raises(ValueError):
        assert_path_usage(path / "inputs.jsonl", purpose)
    with pytest.raises(ValueError):
        assert_dataset_usage(read_rows(path / "inputs.jsonl"), purpose=purpose)


@pytest.mark.parametrize("change", ["empty", "target", "family", "partition", "duplicate", "flags"])
def test_pack_rows_fail_closed(pack, change):
    path, _ = pack
    inputs, answers = read_rows(path / "inputs.jsonl"), read_rows(path / "answers.jsonl")
    if change == "empty":
        inputs, answers = [], []
    elif change == "target":
        answers[0]["generation_target"] = "WRONG"
    elif change == "family":
        answers[0]["family_id"] = "grade-TS"
    elif change == "partition":
        answers[0]["partition"] = "sealed"
    elif change == "duplicate":
        inputs[0] = copy.deepcopy(inputs[1])
    else:
        inputs[0]["training_allowed"] = True
    with pytest.raises(FactContractError):
        builder.validate_rows(inputs, answers)


@pytest.mark.parametrize("payload", ['{"a":1,"a":2}', '{"x": NaN}', '{"x": Infinity}'])
def test_strict_json(payload):
    with pytest.raises(FactContractError):
        builder._loads(payload)


def test_manifest_escape_checked_before_read(tmp_path):
    manifest = {**reference.FLAGS, "schema_version": "internal-fixed-ledger-pack-v0.1",
                "policy_sha256": reference.POLICY_SHA256,
                "source_files_sha256": {p: __import__("hashlib").sha256((builder.POC / p).read_bytes()).hexdigest()
                                        for p in builder.SOURCE_PATHS},
                "files": {"../not-readable": "0" * 64}}
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(FactContractError, match="path_escape"):
        builder.verify_pack(tmp_path)


def test_body_tamper_detected_without_echo(pack, tmp_path, capsys):
    source, _ = pack
    # Small corrupt pack, no expensive full copy required.
    (tmp_path / "manifest.json").write_bytes((source / "manifest.json").read_bytes())
    (tmp_path / "policy.json").write_text("secret-fixture-canary", encoding="utf-8")
    assert builder.main(["--verify", str(tmp_path)]) == 2
    assert "secret-fixture-canary" not in capsys.readouterr().out


def test_audit_controls_and_registered_overlap(pack, monkeypatch):
    path, _ = pack
    observed = []

    def fake_measure(rows, **kwargs):
        observed.extend(rows)
        return {"family_cv": {"mean": 0.25}}

    monkeypatch.setattr(auditor, "measure", fake_measure)
    result = auditor.audit(path)
    assert result["controls"] == {"body_only_hold": 40, "high_grade_release_invariance": 20,
                                  "s2_s3_release_counterfactual": 20, "unknown_release_hold": 20}
    assert len(observed) == 40 and max(len(r["text"]) for r in observed) < 200
    assert result["registered_fixture_overlap"]["matches"] == []
    assert result["customer_accuracy_measured"] is False
    assert result["all_training_pool_overlap_measured"] is False


def test_audit_cannot_write_into_pack(pack):
    path, _ = pack
    assert auditor.main(["--pack", str(path), "--out", str(path / "audit.json")]) == 2


def test_audit_does_not_overwrite(pack, tmp_path):
    path, _ = pack
    output = tmp_path / "existing.json"
    output.write_text("KEEP", encoding="utf-8")
    assert auditor.main(["--pack", str(path), "--out", str(output)]) == 2
    assert output.read_text(encoding="utf-8") == "KEEP"
