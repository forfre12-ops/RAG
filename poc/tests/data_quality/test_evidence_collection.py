"""Fictional snapshot contract regressions, not a customer accuracy dataset."""
from __future__ import annotations

import copy
import json
import traceback
from dataclasses import asdict, replace
from pathlib import Path

import pytest

import collect_policy_evidence as cli
from koipa.evidence_collection import (
    ACCESS_SCOPES, MARKINGS, CollectionContext, CollectionInput, collect_evidence,
)
from koipa.modules.m3_labeling.org_mapping import VALID_MARKINGS, VALID_SCOPES
from koipa.policy_facts import FactContractError, resolve_packet, text_digest, value_digest
from koipa.policy_shadow import evaluate_shadow, policy_digest


@pytest.fixture
def supplied():
    policy, context, cases = cli.demo_inputs()
    return policy, context, dict(cases)


def collect(supplied, name="bound_management"):
    policy, context, cases = supplied
    return collect_evidence(cases[name], context=context, policy=policy)


def test_round_trip_preserves_existing_fact_contract(supplied):
    policy, context, _ = supplied
    result = collect(supplied)
    packet, facts = resolve_packet(result.packet.model_dump(), context.fact_context(),
                                   policy_version=policy.version, policy_sha256=policy_digest(policy))
    assert packet.schema_version == "policy-facts-v1-draft"
    assert facts["access_scope"].value == "approved_only"
    assert facts["security_marking"].state == "unknown"
    assert evaluate_shadow(policy, packet.model_dump(), context=context.fact_context()) == result.report["policy_proposal"]
    assert result.report["packet_sha256"] == value_digest(packet.model_dump())


def test_canonical_vocabulary_matches_existing_mapping_contract():
    assert MARKINGS == set(VALID_MARKINGS)
    assert ACCESS_SCOPES == set(VALID_SCOPES)


def test_no_input_mutation_and_deterministic_private_result(supplied):
    before = copy.deepcopy(supplied)
    first, second = collect(supplied), collect(supplied)
    assert first == second
    first.packet.sources[1].payload["method"] = "mutated-output"
    assert supplied == before
    assert first.packet != second.packet


def test_body_is_not_management_or_public_status(supplied):
    result = collect(supplied, "missing_management")
    assert "대외비" in result.packet.sources[0].payload
    assert all(state == "unknown" for state in result.report["fact_states"].values())
    assert result.report["policy_proposal"]["grade"] is None
    assert result.report["policy_proposal"]["status"] == "needs_evidence"


@pytest.mark.parametrize("fact,value", [
    ("security_marking", "confidential"), ("access_scope", "department"), ("owner_org", "fictional-owner"),
    ("dlp_label", "fictional-label"), ("actual_reader_scope", "fictional-observed-readers"),
])
@pytest.mark.parametrize("state", ["observed", "proven_absent", "unknown"])
def test_explicit_states_and_bound_values(supplied, fact, value, state):
    snapshot = supplied[2]["bound_management"]["management"][0]
    snapshot["fields"] = {fact: {"state": state, "value": value if state == "observed" else None}}
    result = collect(supplied)
    assert result.report["fact_states"][fact] == state
    claims = [claim for claim in result.packet.facts if claim.fact == fact]
    if state == "unknown":
        assert not claims
    else:
        assert claims[0].evidence[0].locator.pointer == f"/fields/{fact}/value"
        source = next(s for s in result.packet.sources if s.source_id == claims[0].evidence[0].source_id)
        assert source.payload["fields"][fact]["value"] == claims[0].value
        assert source.payload["document_revision"] == supplied[1].document_revision


def test_none_vocabulary_is_not_null_and_scope_not_actual_readers(supplied):
    fields = supplied[2]["bound_management"]["management"][0]["fields"]
    fields["security_marking"] = {"state": "observed", "value": "none"}
    result = collect(supplied)
    assert result.report["fact_states"]["security_marking"] == "observed"
    assert result.report["fact_states"]["actual_reader_scope"] == "unknown"


@pytest.mark.parametrize("target", ["extraction", "management"])
@pytest.mark.parametrize("field,value", [
    ("org_id", "different-tenant"), ("document_id", "different-document"),
    ("document_revision", "r2"), ("original_sha256", "a" * 64), ("document_sha256", "b" * 64),
])
def test_document_binding_all_fields(supplied, target, field, value):
    raw = supplied[2]["bound_management"]
    snapshot = raw["extraction"] if target == "extraction" else raw["management"][0]
    snapshot[field] = value
    with pytest.raises(FactContractError, match="collection_document_binding_mismatch"):
        collect(supplied)


@pytest.mark.parametrize("suffix", [" ", "\n", "한", "\u200b"])
def test_no_text_hash_normalization(supplied, suffix):
    supplied[2]["bound_management"]["extraction"]["text"] += suffix
    with pytest.raises(FactContractError, match="collection_text_hash_mismatch"):
        collect(supplied)


@pytest.mark.parametrize("field,value", [
    ("security_marking", "대외비"), ("security_marking", " confidential "),
    ("access_scope", "all"), ("access_scope", "DEPARTMENT"),
])
def test_no_silent_customer_alias_conversion(supplied, field, value):
    supplied[2]["bound_management"]["management"][0]["fields"][field] = {"state": "observed", "value": value}
    with pytest.raises(FactContractError, match="noncanonical_management_value"):
        collect(supplied)


@pytest.mark.parametrize("observation", [
    {"state": "observed", "value": None}, {"state": "unknown", "value": "department"},
    {"state": "proven_absent", "value": "none"}, {"state": "observed"},
    {"state": "observed", "value": False}, {"state": "observed", "value": 0},
    {"state": "observed", "value": " "}, {"state": "proven_absent", "value": ""},
    {"state": "model_estimated", "value": "department"},
])
def test_ambiguous_or_coerced_management_rejected(supplied, observation):
    supplied[2]["bound_management"]["management"][0]["fields"]["access_scope"] = observation
    with pytest.raises(FactContractError):
        collect(supplied)


@pytest.mark.parametrize("field", ["S", "V", "M", "label", "grade", "score", "evaluation_factors", "rule_factors",
                                   "source_type", "public_disclosed", "content_kinds", "has_concrete_parameters"])
def test_only_management_observations_enter_management_adapter(supplied, field):
    supplied[2]["bound_management"]["management"][0]["fields"][field] = {"state": "observed", "value": "invented"}
    with pytest.raises(FactContractError, match="invalid_collection_contract"):
        collect(supplied)


@pytest.mark.parametrize("target", ["extraction", "management"])
@pytest.mark.parametrize("time", ["2026-09-15T04:00:00Z", "2026-09-15T02:00:00", "not-time"])
def test_capture_times_checked(supplied, target, time):
    raw = supplied[2]["bound_management"]
    snapshot = raw["extraction"] if target == "extraction" else raw["management"][0]
    snapshot["captured_at"] = time
    with pytest.raises(FactContractError):
        collect(supplied)


@pytest.mark.parametrize("until", ["2026-09-15T03:00:00Z", "2026-09-15T00:00:00Z", "not-time"])
def test_invalid_expiry_not_ignored(supplied, until):
    supplied[2]["bound_management"]["management"][0]["valid_until"] = until
    with pytest.raises(FactContractError):
        collect(supplied)


def test_no_expiry_does_not_invent_freshness(supplied):
    supplied[2]["bound_management"]["management"][0].pop("valid_until")
    report = collect(supplied).report
    assert report["management_without_expiry"] == 1
    assert report["freshness_policy_verified"] is False


def test_conflict_is_order_independent_not_latest_wins(supplied):
    raw = supplied[2]["conflicting_snapshots"]
    raw["management"][1]["captured_at"] = "2026-09-15T02:30:00Z"
    first = collect(supplied, "conflicting_snapshots")
    raw["management"].reverse()
    second = collect(supplied, "conflicting_snapshots")
    for result in (first, second):
        assert result.report["conflicting_facts"] == ["access_scope"]
        assert result.report["policy_proposal"]["grade"] is None
        assert result.report["policy_proposal"]["status"] == "needs_evidence_conflict_review"
    assert first.report["fact_states"] == second.report["fact_states"]


def test_duplicate_snapshot_identity_rejected(supplied):
    raw = supplied[2]["bound_management"]
    raw["management"].append(copy.deepcopy(raw["management"][0]))
    with pytest.raises(FactContractError, match="duplicate_management_snapshot"):
        collect(supplied)


def test_independent_equal_claims_combine_without_conflict(supplied):
    raw = supplied[2]["conflicting_snapshots"]
    raw["management"][1]["fields"]["access_scope"]["value"] = "approved_only"
    result = collect(supplied, "conflicting_snapshots")
    assert result.report["conflicting_facts"] == []
    assert len(result.report["policy_proposal"]["facts"]["access_scope"]["source_ids"]) == 2


@pytest.mark.parametrize("patch,reason", [
    ({"completeness": "unknown"}, "extraction_completeness_unknown"),
    ({"completeness": "incomplete"}, "extraction_completeness_incomplete"),
    ({"table_coverage": "unknown"}, "table_coverage_unknown"),
    ({"table_coverage": "incomplete"}, "table_coverage_incomplete"),
    ({"pages": 1, "total_pages": 3}, "extraction_pages_incomplete"),
    ({"pages": 0, "total_pages": 0}, "extraction_pages_incomplete"),
    ({"pages": 1}, "extraction_page_coverage_unknown"),
    ({"total_pages": 2}, "extraction_page_coverage_unknown"),
    ({"error": "fictional-secret-error"}, "extraction_error_reported"),
    ({"warnings": ["fictional-secret-warning"], "quality": 1.0}, "extraction_warnings_reported"),
])
def test_incomplete_extraction_cannot_export_low_grade_packet(supplied, patch, reason):
    supplied[2]["bound_management"]["extraction"].update(patch)
    result = collect(supplied)
    assert result.report["status"] == "held_extraction"
    assert reason in result.report["hold_reasons"]
    assert result.packet is None
    assert result.report["policy_proposal"] is None
    assert result.report["packet_sha256"] is None
    assert result.report["packet_available"] is False


@pytest.mark.parametrize("body", ["", " \n\t", "\ufffc\ufeff\u200b"])
def test_empty_text_is_held_even_with_matching_hashes(supplied, body):
    policy, context, cases = supplied
    raw = cases["bound_management"]
    raw["extraction"]["text"] = body
    digest = text_digest(body)
    for snapshot in [raw["extraction"], *raw["management"]]:
        snapshot["document_sha256"] = digest
    context = context.model_copy(update={"document_sha256": digest})
    result = collect_evidence(raw, context=context, policy=policy)
    assert result.packet is None
    assert "extracted_text_empty" in result.report["hold_reasons"]


@pytest.mark.parametrize("patch", [
    {"pages": 3, "total_pages": 2}, {"pages": True}, {"pages": -1}, {"quality": float("nan")},
    {"completeness": True}, {"ocr_used": "false"}, {"document_sha256": "not-a-hash"},
])
def test_invalid_extraction_contract(supplied, patch):
    supplied[2]["bound_management"]["extraction"].update(patch)
    with pytest.raises(FactContractError):
        collect(supplied)


def test_unknown_coverage_default_is_not_complete(supplied):
    extraction = supplied[2]["bound_management"]["extraction"]
    extraction.pop("completeness")
    extraction.pop("table_coverage")
    assert collect(supplied).report["hold_reasons"] == ["extraction_completeness_unknown", "table_coverage_unknown"]


def test_estimates_do_not_become_facts(supplied):
    raw = supplied[2]["missing_management"]
    raw["estimates"] = [{"fact": name, "value": value, "origin": origin, "producer_ref": "fictional-producer"}
                        for name, value, origin in [("access_scope", "approved_only", "model_estimated"),
                                                    ("public_disclosed", False, "rule_estimated")]]
    result = collect(supplied, "missing_management")
    assert result.report["estimate_count"] == 2
    assert result.report["estimates_used_for_policy"] is False
    assert result.report["fact_states"]["public_disclosed"] == "unknown"
    assert result.report["policy_proposal"]["grade"] is None


def test_lineage_and_output_do_not_claim_authentication(supplied):
    result = collect(supplied)
    assert result.report["lineage"]["original_binary_sha256"] == supplied[1].original_sha256
    for key in ("original_binary_bytes_verified", "evidence_authenticity_verified", "semantic_truth_verified",
                "extraction_completeness_independently_verified", "policy_approval_verified", "training_allowed",
                "model_evaluation_allowed", "customer_accuracy_measured", "automation_allowed", "finalized"):
        assert result.report[key] is False
    encoded = json.dumps(result.report, ensure_ascii=False)
    for secret in (supplied[2]["bound_management"]["extraction"]["text"], "approved_only",
                   "fictional-not-an-authenticated-system", "fictional-not-a-source-connection"):
        assert secret not in encoded


def test_invalid_policy_rejected_even_when_extraction_held(supplied):
    policy, context, cases = supplied
    with pytest.raises(FactContractError):
        collect_evidence(cases["incomplete_extraction"], context=context,
                         policy=replace(policy, org_id="other-org"))


def test_model_construct_does_not_bypass_nested_validation(supplied):
    policy, context, cases = supplied
    parsed = CollectionInput.model_validate(cases["bound_management"])
    parsed.management[0].fields["access_scope"] = {"state": "observed", "value": 0}
    with pytest.raises(FactContractError, match="invalid_collection_contract"):
        collect_evidence(parsed, context=context, policy=policy)


def test_invalid_context_and_schema_errors_hide_raw_text(supplied):
    policy, context, cases = supplied
    raw = cases["bound_management"]
    raw["extraction"]["extra"] = "DO-NOT-PRINT-THIS-SECRET"
    try:
        collect_evidence(raw, context=context, policy=policy)
    except FactContractError:
        assert "DO-NOT-PRINT" not in traceback.format_exc()
    else:
        pytest.fail("unexpected acceptance")
    raw["extraction"].pop("extra")
    invalid_context = CollectionContext.model_construct(**{**context.model_dump(), "document_revision": 5})
    with pytest.raises(FactContractError):
        collect_evidence(raw, context=invalid_context, policy=policy)


def test_pure_adapter_does_not_read_or_connect(supplied, monkeypatch):
    import socket

    def forbidden(*_args, **_kwargs):
        raise AssertionError("unexpected I/O")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(Path, "read_text", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    assert collect(supplied).packet is not None


def files(tmp_path, supplied, name="bound_management"):
    policy, context, cases = supplied
    policy_json = asdict(policy)
    policy_json["policy_version"] = policy_json.pop("version")
    paths = {}
    for role, value in (("policy", policy_json), ("snapshot", cases[name]), ("context", context.model_dump())):
        paths[role] = tmp_path / (role + ".json")
        paths[role].write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    argv = [item for role, path in paths.items() for item in ("--" + role, str(path))]
    return paths, argv


@pytest.mark.parametrize("name,expected", [
    ("bound_management", 0), ("missing_management", 3), ("conflicting_snapshots", 3), ("incomplete_extraction", 3),
])
def test_cli_statuses_and_explicit_packet_export(tmp_path, supplied, capsys, name, expected):
    paths, argv = files(tmp_path, supplied, name)
    before = {role: path.read_bytes() for role, path in paths.items()}
    out, packet = tmp_path / "report.json", tmp_path / "packet.json"
    assert cli.main([*argv, "--out", str(out), "--packet-out", str(packet)]) == expected
    assert {role: path.read_bytes() for role, path in paths.items()} == before
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["training_allowed"] is False
    assert packet.exists() == (name != "incomplete_extraction")
    if packet.exists():
        policy, context, _ = supplied
        shadow = evaluate_shadow(policy, json.loads(packet.read_text(encoding="utf-8")), context=context.fact_context())
        assert shadow == report["policy_proposal"]
    summary = capsys.readouterr().out
    assert supplied[2][name]["extraction"]["text"] not in summary
    assert "approved_only" not in out.read_text(encoding="utf-8")


def test_cli_default_does_not_write_files(tmp_path, supplied, capsys):
    _, argv = files(tmp_path, supplied)
    before = set(tmp_path.iterdir())
    assert cli.main(argv) == 0
    assert set(tmp_path.iterdir()) == before
    assert json.loads(capsys.readouterr().out)["packet_exported"] is False


@pytest.mark.parametrize("flag", ["--out", "--packet-out"])
def test_cli_existing_output_untouched(tmp_path, supplied, capsys, flag):
    _, argv = files(tmp_path, supplied)
    existing = tmp_path / "preserved.json"
    existing.write_bytes(b"unchanged")
    assert cli.main([*argv, flag, str(existing)]) == 2
    assert existing.read_bytes() == b"unchanged"
    assert "output_exists" in capsys.readouterr().out


def test_cli_overlapping_new_outputs_rejected(tmp_path, supplied, capsys):
    _, argv = files(tmp_path, supplied)
    out = tmp_path / "same.json"
    assert cli.main([*argv, "--out", str(out), "--packet-out", str(out)]) == 2
    assert not out.exists()
    assert "output_paths_overlap" in capsys.readouterr().out


@pytest.mark.parametrize("payload", ['{"duplicate":1,"duplicate":2}', '{"x":NaN}', '{"x":Infinity}',
                                      '{"SECRET-DO-NOT-PRINT": "missing-end', '[]'])
def test_cli_bad_json_no_sensitive_error(tmp_path, supplied, capsys, payload):
    paths, argv = files(tmp_path, supplied)
    paths["snapshot"].write_text(payload, encoding="utf-8")
    assert cli.main(argv) == 2
    assert "SECRET-DO-NOT-PRINT" not in capsys.readouterr().out


def test_cli_rejects_changed_input_before_any_export(tmp_path, supplied, monkeypatch, capsys):
    paths, argv = files(tmp_path, supplied)
    original = cli.collect_evidence

    def changing(*args, **kwargs):
        result = original(*args, **kwargs)
        paths["snapshot"].write_text("{}", encoding="utf-8")
        return result

    monkeypatch.setattr(cli, "collect_evidence", changing)
    out = tmp_path / "new.json"
    assert cli.main([*argv, "--packet-out", str(out)]) == 2
    assert not out.exists()
    assert "input_changed_during_collection" in capsys.readouterr().out


@pytest.mark.parametrize("argv", [[], ["--demo", "--schema"], ["--demo", "--packet-out", "never-created.json"],
                                   ["--schema", "--snapshot", "not-read.json"]])
def test_cli_invalid_modes_fail(argv, capsys):
    assert cli.main(argv) == 2
    assert "INVALID_COLLECTION_INPUT" in capsys.readouterr().out


def test_cli_demo_and_schema_are_not_approval(tmp_path, capsys):
    out = tmp_path / "demo.json"
    assert cli.main(["--demo", "--out", str(out)]) == 0
    demo = json.loads(out.read_text(encoding="utf-8"))
    assert demo["synthetic_cases"] == 5 and demo["real_documents"] == 0
    assert not demo["automation_allowed"]
    schema = tmp_path / "schema.json"
    assert cli.main(["--schema", "--out", str(schema)]) == 0
    assert {"snapshot", "context"} == json.loads(schema.read_text(encoding="utf-8")).keys()
    capsys.readouterr()
