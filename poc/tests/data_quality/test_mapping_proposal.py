"""Fictional mapping/lineage cases, never customer ground truth or approvals."""
from __future__ import annotations

import copy
import json
import traceback
from pathlib import Path

import pytest
from pydantic import ValidationError

import check_customer_mapping as cli
from koipa.evidence_collection import ManagementSnapshot
from koipa.mapping_proposal import MappingContext, MappingPackage, mapping_digest, preview_mapping, verify_preview
from koipa.policy_facts import FactContractError, FactPacket, value_digest


@pytest.fixture
def case():
    _, package, snapshot, context = cli.demo_inputs()[0]
    return package, snapshot, context


def run(case):
    return preview_mapping(case[0], case[1], context=case[2])


def rebind(case):
    package, snapshot, context = case
    return package, snapshot, context.model_copy(update={"mapping_sha256": mapping_digest(package)})


def rehash_snapshot(case):
    case[1]["payload_sha256"] = value_digest(case[1]["payload"])


def test_exact_mapping_has_both_provenance_links_and_replays(case):
    result = run(case)
    row = result.artifact["proposals"][0]
    assert row["status"] == "mapped_candidate"
    assert row["raw_value"] == "가상-대외비"
    assert row["canonical_candidate"] == "confidential"
    assert row["raw_evidence"]["pointer"] == "/marking"
    assert row["matched_rules"][0]["evidence"][0]["locator"]["pointer"] == "/rules/0"
    assert verify_preview(result.artifact)["replay_verified"] is True


def test_no_mutation_private_outputs_and_deterministic(case):
    original = copy.deepcopy(case)
    first, second = run(case), run(case)
    assert first == second
    first.artifact["inputs"]["snapshot"]["payload"]["marking"] = "output-change"
    first.report["fields"][0]["status"] = "output-change"
    assert first.artifact["report"]["fields"][0]["status"] == "mapped_candidate"
    assert second.artifact["inputs"]["snapshot"]["payload"]["marking"] == "가상-대외비"
    assert case == original


@pytest.mark.parametrize("term", ["대외비", "가상-대외비 ", " 가상-대외비", "confidential", "none", "CONFIDENTIAL"])
def test_exact_only_no_alias_or_canonical_passthrough(case, term):
    case[1]["payload"]["marking"] = term
    rehash_snapshot(case)
    row = run(case).artifact["proposals"][0]
    assert row["status"] == "unmapped" and row["canonical_candidate"] is None


def test_unknown_absence_and_unmapped_are_distinct(case):
    case[1]["fields"].pop("security_marking")
    assert run(case).artifact["proposals"][0]["status"] == "unknown"
    case[1]["fields"]["security_marking"] = {"state": "unknown", "pointer": None}
    assert run(case).artifact["proposals"][0]["raw_evidence"] is None
    case[1]["fields"]["security_marking"] = {"state": "proven_absent", "pointer": "/marking"}
    case[1]["payload"]["marking"] = None
    rehash_snapshot(case)
    row = run(case).artifact["proposals"][0]
    assert row["status"] == "absence_claim_preserved" and row["raw_evidence"] is not None


@pytest.mark.parametrize("value", [None, "", " ", False, 0, [], {"text": "가상-대외비"}])
def test_invalid_observed_raw_values_rejected(case, value):
    case[1]["payload"]["marking"] = value
    rehash_snapshot(case)
    with pytest.raises(FactContractError, match="raw_observation_requires_string"):
        run(case)


@pytest.mark.parametrize("field", [
    {"state": "proven_absent", "pointer": "/marking"}, {"state": "unknown", "pointer": "/marking"},
    {"state": "observed", "pointer": None}, {"state": "observed"},
    {"state": "model_estimated", "pointer": "/marking"},
])
def test_invalid_state_pointer_contract(case, field):
    case[1]["fields"]["security_marking"] = field
    with pytest.raises(FactContractError):
        run(case)


@pytest.mark.parametrize("pointer", ["marking", "/missing", "/~2bad", "/marking/child"])
def test_invalid_raw_location_rejected(case, pointer):
    case[1]["fields"]["security_marking"]["pointer"] = pointer
    with pytest.raises(FactContractError):
        run(case)


def test_escaped_pointer_and_unicode_value(case):
    case[1]["payload"]["a/b~c"] = ["가상-대외비"]
    case[1]["fields"]["security_marking"]["pointer"] = "/a~1b~0c/0"
    rehash_snapshot(case)
    assert run(case).artifact["proposals"][0]["canonical_candidate"] == "confidential"


@pytest.mark.parametrize("field,value", [
    ("org_id", "other-org"), ("document_id", "other-doc"), ("document_revision", "r2"),
    ("original_sha256", "a" * 64), ("document_sha256", "b" * 64),
])
def test_document_binding(case, field, value):
    case[1][field] = value
    with pytest.raises(FactContractError, match="mapping_document_binding_mismatch"):
        run(case)


@pytest.mark.parametrize("field,value", [
    ("org_id", "other-org"), ("mapping_id", "other-map"), ("version", "new-version"),
    ("declared_status", "review_requested"),
])
def test_context_rejects_mapping_replacement(case, field, value):
    case[0][field] = value
    with pytest.raises(FactContractError, match="mapping_org_mismatch|mapping_version_binding_mismatch"):
        run(case)


@pytest.mark.parametrize("change,expected", [
    ({"effective_at": "2026-09-16T00:00:00Z"}, "mapping_not_effective"),
    ({"valid_until": "2026-09-15T03:00:00Z"}, "mapping_expired"),
    ({"declared_status": "retired"}, "mapping_retired"),
])
def test_inactive_mapping_no_canonical_candidate(case, change, expected):
    case[0].update(change)
    result = run(rebind(case))
    assert result.report["status"] == "blocked_mapping"
    assert result.report["mapping_block_reason"] == expected
    assert all(row["canonical_candidate"] is None for row in result.artifact["proposals"])


@pytest.mark.parametrize("change", [
    {"effective_at": "2026-09-15T00:00:00"}, {"effective_at": "bad-date"},
    {"valid_until": "2026-09-15T00:00:00Z"}, {"declared_status": "approved"},
    {"approval_record_ref": "unexpected-in-draft"},
])
def test_invalid_mapping_lifecycle(case, change):
    case[0].update(change)
    with pytest.raises(FactContractError):
        run(rebind(case))


@pytest.mark.parametrize("change", [
    {"captured_at": "2026-09-15T04:00:00Z"}, {"captured_at": "2026-09-15T01:00:00"},
    {"valid_until": "2026-09-15T03:00:00Z"}, {"valid_until": "2026-09-14T00:00:00Z"},
])
def test_raw_snapshot_period_checked(case, change):
    case[1].update(change)
    with pytest.raises(FactContractError):
        run(case)


def test_expiry_not_invented(case):
    case[1].pop("valid_until")
    assert run(case).report["snapshot_without_expiry"] is True


@pytest.mark.parametrize("target", ["raw_payload", "mapping_payload", "source_hash", "record_hash", "record_value", "source_org"])
def test_changed_evidence_rejected_even_with_new_package_digest(case, target):
    package, snapshot, _ = case
    if target == "raw_payload":
        snapshot["payload"]["marking"] = "tampered"
    elif target == "mapping_payload":
        package["sources"][0]["payload"]["rules"][0]["raw_value"] = "tampered"
    elif target == "source_hash":
        package["rules"][0]["evidence"][0]["source_sha256"] = "a" * 64
    elif target == "record_hash":
        package["rules"][0]["evidence"][0]["value_sha256"] = "a" * 64
    elif target == "record_value":
        package["rules"][0]["canonical_value"] = "secret"
    else:
        package["sources"][0]["org_id"] = "other-org"
    with pytest.raises(FactContractError):
        run(rebind(case))


@pytest.mark.parametrize("change", ["duplicate_source", "duplicate_rule", "missing_source", "future_source", "text_span", "bad_canonical"])
def test_invalid_mapping_rule_reference(case, change):
    package = case[0]
    if change == "duplicate_source":
        package["sources"].append(copy.deepcopy(package["sources"][0]))
    elif change == "duplicate_rule":
        package["rules"].append(copy.deepcopy(package["rules"][0]))
    elif change == "missing_source":
        package["sources"] = []
    elif change == "future_source":
        package["sources"][0]["captured_at"] = "2026-09-15T04:00:00Z"
    elif change == "text_span":
        package["rules"][0]["evidence"][0]["locator"] = {"kind": "text_span", "start": 0, "end": 1}
    else:
        package["rules"][0]["canonical_value"] = "TOP_SECRET"
    with pytest.raises(FactContractError):
        run(rebind(case))


def test_conflict_order_independent_and_global_conflict_report():
    _, package, snapshot, context = cli.demo_inputs()[3]
    first = preview_mapping(package, snapshot, context=context)
    package["rules"].reverse()
    context = context.model_copy(update={"mapping_sha256": mapping_digest(package)})
    second = preview_mapping(package, snapshot, context=context)
    assert first.artifact["proposals"] == second.artifact["proposals"]
    assert first.artifact["proposals"][0]["status"] == "conflict"
    assert first.artifact["proposals"][0]["canonical_candidate"] is None
    snapshot["fields"].pop("security_marking")
    result = preview_mapping(package, snapshot, context=context)
    assert result.report["package_selector_conflicts"] == 1
    assert result.artifact["proposals"][1]["status"] == "mapped_candidate"


def test_duplicate_selector_same_target_keeps_all_rules(case):
    another = copy.deepcopy(case[0]["rules"][0])
    another["rule_id"] = "independent-same-rule"
    case[0]["rules"].append(another)
    result = run(rebind(case))
    assert result.report["package_selector_conflicts"] == 0
    assert len(result.artifact["proposals"][0]["matched_rules"]) == 2


@pytest.mark.parametrize("origin", ["system_export", "request_metadata", "unverified_stored_metadata"])
def test_origin_never_grants_authenticity(case, origin):
    case[1]["origin"] = origin
    result = run(case)
    assert result.report["snapshot_origin"] == origin
    assert result.report["source_authenticity_verified"] is False
    assert result.report["fact_export_allowed"] is False


def test_claimed_approval_not_authorization_or_fact_export():
    _, package, snapshot, context = cli.demo_inputs()[-1]
    result = preview_mapping(package, snapshot, context=context)
    assert result.report["approval_claim_present"] is True
    for key in ("approval_authenticity_verified", "fact_export_allowed", "management_snapshot_export_allowed",
                "training_allowed", "model_evaluation_allowed", "automation_allowed", "finalized"):
        assert result.report[key] is False
    for model in (FactPacket, ManagementSnapshot):
        with pytest.raises(ValidationError):
            model.model_validate(result.artifact)


def test_empty_package_no_fallback(case):
    case[0]["rules"], case[0]["sources"] = [], []
    assert all(row["status"] == "unmapped" for row in run(rebind(case)).artifact["proposals"])


@pytest.mark.parametrize("name", ["grade", "S", "V", "M", "public_disclosed", "actual_reader_scope"])
def test_unsupported_fact_names_fail(case, name):
    case[1]["fields"][name] = {"state": "observed", "pointer": "/marking"}
    with pytest.raises(FactContractError):
        run(case)


def test_non_json_nested_values_rejected(case):
    case[1]["payload"]["extra"] = ("a", "b")
    rehash_snapshot(case)
    with pytest.raises(FactContractError, match="non_json_mapping_payload"):
        run(case)


@pytest.mark.parametrize("target", ["candidate", "authority_flag", "raw_value", "extra_root", "extra_inputs"])
def test_replay_rejects_tampering(case, target):
    artifact = run(case).artifact
    if target == "candidate":
        artifact["proposals"][0]["canonical_candidate"] = "secret"
    elif target == "authority_flag":
        artifact["report"]["fact_export_allowed"] = True
    elif target == "raw_value":
        artifact["inputs"]["snapshot"]["payload"]["marking"] = "tampered"
    elif target == "extra_root":
        artifact["approved"] = True
    else:
        artifact["inputs"]["approved"] = True
    with pytest.raises(FactContractError):
        verify_preview(artifact)


def test_model_construct_cannot_bypass_and_no_secret_errors(case):
    package = MappingPackage.model_validate(case[0])
    package.rules[0].evidence[0].locator.__dict__["pointer"] = "PRIVATE-INVALID-POINTER"
    try:
        preview_mapping(package, case[1], context=case[2])
    except FactContractError:
        assert "PRIVATE-INVALID-POINTER" not in traceback.format_exc()
    else:
        pytest.fail("malformed constructed instance accepted")
    bad_context = MappingContext.model_construct(**{**case[2].model_dump(), "mapping_version": 42})
    with pytest.raises(FactContractError):
        preview_mapping(case[0], case[1], context=bad_context)


def test_no_network_or_files_in_preview(case, monkeypatch):
    import socket

    def forbidden(*_args, **_kwargs):
        raise AssertionError("unexpected I/O")

    monkeypatch.setattr(Path, "read_bytes", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    assert run(case).report["binding_verified"] is True


def files(tmp_path, case):
    paths, args = {}, []
    for name, payload in zip(("mapping", "snapshot", "context"), (case[0], case[1], case[2].model_dump())):
        path = tmp_path / (name + ".json")
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        paths[name] = path
        args.extend(["--" + name, str(path)])
    return paths, args


def test_cli_sensitive_export_and_report_redaction_and_replay(tmp_path, case, capsys):
    paths, args = files(tmp_path, case)
    before = {key: path.read_bytes() for key, path in paths.items()}
    report, preview = tmp_path / "report.json", tmp_path / "preview.json"
    assert cli.main([*args, "--out", str(report), "--preview-out", str(preview)]) == 3
    assert {key: path.read_bytes() for key, path in paths.items()} == before
    displayed = capsys.readouterr().out + report.read_text(encoding="utf-8")
    for secret in ("가상-대외비", "confidential", "fictional-not-an-authenticated-provider"):
        assert secret not in displayed
    assert "가상-대외비" in preview.read_text(encoding="utf-8")
    assert cli.main(["--verify-preview", str(preview)]) == 0
    assert json.loads(capsys.readouterr().out)["replay_verified"] is True


def test_cli_no_output_by_default(tmp_path, case, capsys):
    _, args = files(tmp_path, case)
    before = set(tmp_path.iterdir())
    assert cli.main(args) == 3
    assert set(tmp_path.iterdir()) == before
    capsys.readouterr()


@pytest.mark.parametrize("flag", ["--out", "--preview-out"])
def test_existing_output_not_overwritten(tmp_path, case, capsys, flag):
    _, args = files(tmp_path, case)
    path = tmp_path / "existing.json"
    path.write_bytes(b"unchanged")
    assert cli.main([*args, flag, str(path)]) == 2
    assert path.read_bytes() == b"unchanged"
    assert "output_exists" in capsys.readouterr().out


def test_overlapping_output_paths_rejected(tmp_path, case, capsys):
    _, args = files(tmp_path, case)
    path = tmp_path / "same.json"
    assert cli.main([*args, "--out", str(path), "--preview-out", str(path)]) == 2
    assert not path.exists()
    assert "mapping_outputs_overlap" in capsys.readouterr().out


@pytest.mark.parametrize("payload", ['{"x":1,"x":2}', '{"x":NaN}', '[]', '{"DO-NOT-PRINT-SECRET": '])
def test_cli_bad_json_opaque_errors(tmp_path, case, capsys, payload):
    paths, args = files(tmp_path, case)
    paths["mapping"].write_text(payload, encoding="utf-8")
    assert cli.main(args) == 2
    assert "DO-NOT-PRINT-SECRET" not in capsys.readouterr().out


def test_changed_input_blocks_export(tmp_path, case, monkeypatch, capsys):
    paths, args = files(tmp_path, case)
    original = cli.preview_mapping

    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        paths["snapshot"].write_text("{}", encoding="utf-8")
        return result

    monkeypatch.setattr(cli, "preview_mapping", changed)
    out = tmp_path / "new.json"
    assert cli.main([*args, "--preview-out", str(out)]) == 2
    assert not out.exists()
    assert "mapping_input_changed_during_check" in capsys.readouterr().out


@pytest.mark.parametrize("args", [[], ["--demo", "--schema"], ["--demo", "--mapping", "not-read.json"],
                                   ["--verify-preview", "not-read.json", "--preview-out", "not-created.json"]])
def test_invalid_modes(args, capsys):
    assert cli.main(args) == 2
    capsys.readouterr()


def test_schema_and_fictional_demo(tmp_path, capsys):
    demo, schema = tmp_path / "demo.json", tmp_path / "schema.json"
    assert cli.main(["--demo", "--out", str(demo)]) == 0
    report = json.loads(demo.read_text(encoding="utf-8"))
    assert len(report["results"]) == 6 and report["real_documents"] == 0
    assert not report["training_allowed"]
    assert cli.main(["--schema", "--out", str(schema)]) == 0
    assert set(json.loads(schema.read_text(encoding="utf-8"))) == {"mapping", "snapshot", "context"}
    capsys.readouterr()
