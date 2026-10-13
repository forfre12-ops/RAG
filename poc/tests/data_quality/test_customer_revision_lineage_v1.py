"""Revision versions are not new manuscripts or newly blind evaluation inputs."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from customer_benchmark_drafts import build_drafts
import audit_customer_revision_lineage_v1 as cli
import koipa.customer_revision_lineage_v1 as revision
from koipa.customer_benchmark import FLAGS
from koipa.customer_eval_partition_v1 import audit_exposure, build_exposure_ledger
from koipa.customer_guide_reference import POLICY_SHA256, build_case
from koipa.policy_facts import FactContractError, text_digest, value_digest


def _rebind_input(record):
    data = record["input"]
    data["doc_id"] = "doc-"+value_digest({"text": data["text"], "context": data["context"]})[:24]
    record["input_sha256"] = value_digest(data)


def _repair(triple, replace=True):
    parent, answer, evidence = triple
    child, updated_answer, updated_evidence = copy.deepcopy(triple)
    edits = []
    if replace:
        start = parent["input"]["text"].rindex("분말 R")+len("분말 ")
        edits = [{"start": start, "end": start+1, "before": "R", "after": "느티", "target_start": start,
            "target_end": start+2, "noun": "분말", "alias": "느티", "reason": "가상 식별자를 중립 별칭으로 교체"}]
        child["input"]["text"] = parent["input"]["text"][:start]+"느티"+parent["input"]["text"][start+1:]
        for claim in child["claims"]:
            quote_is_claim = claim["claim"] == claim["quote"]
            claim["start"] += claim["start"] > start
            claim["end"] += claim["end"] > start
            claim["quote"] = child["input"]["text"][claim["start"]:claim["end"]]
            if quote_is_claim:
                claim["claim"] = claim["quote"]
            claim["sha256"] = text_digest(claim["quote"])
        _rebind_input(child)
        updated_answer.update(doc_id=child["input"]["doc_id"], input_sha256=child["input_sha256"])
        updated_evidence.update(doc_id=child["input"]["doc_id"], input_sha256=child["input_sha256"],
            body_sha256=text_digest(child["input"]["text"]))
    row = {**FLAGS, **{k: parent[k] for k in revision.FAMILIES}, "parent_doc_id": parent["input"]["doc_id"],
        "child_doc_id": child["input"]["doc_id"], "parent_input_sha256": parent["input_sha256"],
        "child_input_sha256": child["input_sha256"], "parent_body_sha256": text_digest(parent["input"]["text"]),
        "child_body_sha256": text_digest(child["input"]["text"]), "parent_answer_sha256": value_digest(answer),
        "child_answer_sha256": value_digest(updated_answer), "parent_evidence_sha256": value_digest(evidence),
        "child_evidence_sha256": value_digest(updated_evidence), "policy_sha256": POLICY_SHA256,
        "context_sha256": value_digest(parent["input"]["context"]), "changed": replace,
        "repair_kind": "identifier_alias_repair", "edits": edits, "new_document_count": 0}
    return (child, updated_answer, updated_evidence), row


@pytest.fixture
def material():
    facts = {"public_exact_body": False, "obtainable_without_holder": False, "ordinary_access_difficult": True,
        "cost_krw": 30000000, "person_hours": 600, "economic_utility": True, "investment_scope_exact": True,
        "secrecy_manageable": True, "all_staff_knows": False, "business_need_only": True,
        "individual_approval": True, "access_enforced": True, "release_authorized": False, "other_risk_present": False}
    triples = []
    for draft in build_drafts()[:4]:
        draft = copy.deepcopy(draft)
        start = len(draft["input"]["text"])+1
        quote = "분말 R의 관측은 별도 표에 기록합니다."
        draft["input"]["text"] += "\n"+quote
        draft["claims"].append({"name": "alias-observation", "claim": quote, "quote": quote,
            "start": start, "end": start+len(quote), "sha256": text_digest(quote), "status": "authored_binding_only"})
        _rebind_input(draft)
        triples.append(build_case(draft, facts, "해당 정보에 직접 귀속된 비용과 접근 관리 상태를 가정한 단위 시험 사례이며 실제 고객 관측이나 독립 검수 결과가 아닙니다."))
    changed, line_changed = _repair(triples[0])
    unchanged, line_unchanged = _repair(triples[1], False)
    original = [t[0] for t in triples]
    ledger = build_exposure_ledger(original, source_ref="test-source", source_sha256="1"*64, reason="development_authoring")
    links = [{"members": [triples[0][0]["input"]["doc_id"], triples[2][0]["input"]["doc_id"]], "reason": "두 독립 원고의 연결을 시험하기 위한 명시적 계열 기록"}]
    prior = audit_exposure(original, ledger, expected_ledger_sha256=ledger["ledger_sha256"], semantic_links=links)
    revised = [changed, unchanged, *copy.deepcopy(triples[2:])]
    return {"original_records": original, "original_answers": [t[1] for t in triples],
        "revised_records": [t[0] for t in revised], "revised_answers": [t[1] for t in revised],
        "lineage": [line_changed, line_unchanged], "original_evidence": [t[2] for t in triples],
        "revised_evidence": [t[2] for t in revised], "panel_parent_ids": [t[0]["input"]["doc_id"] for t in triples[:2]],
        "ledger": ledger, "expected_ledger_sha256": ledger["ledger_sha256"],
        "parent_manifest_sha256": revision.PARENT_MANIFEST_SHA256, "source_ref": "test-revision",
        "source_sha256": "2"*64, "semantic_links": links, "expected_document_count": 4,
        "expected_panel_count": 2, "expected_previous_groups": len(prior["groups"])}


def _audit(material):
    return revision.audit_revision(**material)


def test_active_replacement_and_exposure_propagation(material):
    before = copy.deepcopy(material)
    result = _audit(material)
    assert result["active_documents"] == 4 and result["repair_parent_documents"] == 2
    assert result["changed_document_versions"] == result["unchanged_panel_documents"] == 1
    assert result["new_manuscripts"] == 0
    assert result["adoption_allowed"] is result["authoritative_parent_replaced"] is False
    assert result["active_documents_role"] == "proposed_diagnostic_views_not_authoritative_corpus"
    assert result["physical_exposure_records_as_document_versions"] == 5
    assert result["exposure_audit"]["forbidden_evaluation_count"] == 4
    assert result["active_groups"] == result["previous_groups"]
    old_rows = {r["doc_id"]: r for r in material["ledger"]["records"]}
    for row in result["exposure_ledger"]["records"]:
        if row["doc_id"] in old_rows:
            assert row == old_rows[row["doc_id"]]
    parent = material["lineage"][0]["parent_doc_id"]
    child = material["lineage"][0]["child_doc_id"]
    assert result["retired_parent_ids"] == [parent]
    assert child in result["semantic_links_remapped"][0]["members"]
    assert parent not in result["semantic_links_remapped"][0]["members"]
    assert all(result[k] is False for k in FLAGS)
    assert material == before and not result["alias_semantics_independently_certified_here"]


def test_all_unchanged_keeps_exact_ledger_no_empty_extension(material):
    material["revised_records"] = copy.deepcopy(material["original_records"])
    material["revised_answers"] = copy.deepcopy(material["original_answers"])
    material["revised_evidence"] = copy.deepcopy(material["original_evidence"])
    triple = tuple(material[k][0] for k in ("original_records", "original_answers", "original_evidence"))
    material["lineage"][0] = _repair(triple, False)[1]
    result = _audit(material)
    assert result["changed_document_versions"] == 0 and result["exposure_ledger"] == material["ledger"]


@pytest.mark.parametrize("mutation", ["missing", "extra", "duplicate", "unknown_parent", "reused_child"])
def test_panel_coverage_failures(material, mutation):
    if mutation == "missing":
        material["lineage"].pop()
    elif mutation == "extra":
        material["lineage"].append(copy.deepcopy(material["lineage"][0]))
    elif mutation == "duplicate":
        material["lineage"][1] = copy.deepcopy(material["lineage"][0])
    elif mutation == "unknown_parent":
        material["lineage"][0]["parent_doc_id"] = "doc-"+"f"*24
    else:
        material["lineage"][0]["child_doc_id"] = material["lineage"][1]["child_doc_id"]
    with pytest.raises(FactContractError, match="coverage|lineage_ids"):
        _audit(material)


@pytest.mark.parametrize("key", ["parent_input_sha256", "child_input_sha256", "parent_body_sha256", "child_body_sha256",
    "parent_answer_sha256", "child_answer_sha256", "parent_evidence_sha256", "child_evidence_sha256", "policy_sha256", "context_sha256"])
def test_every_lineage_binding_is_checked(material, key):
    material["lineage"][0][key] = "f"*64
    with pytest.raises(FactContractError, match="sidecar_hash_mismatch"):
        _audit(material)


@pytest.mark.parametrize("value", [True, 0, None, "false"])
def test_permission_not_promoted(material, value):
    material["lineage"][0]["training_allowed"] = value
    with pytest.raises(FactContractError, match="permission"):
        _audit(material)


@pytest.mark.parametrize("key, value", [("changed", False), ("changed", 1), ("new_document_count", 1),
    ("new_document_count", False), ("repair_kind", "new_manuscript")])
def test_revision_role_cannot_count_new_documents(material, key, value):
    material["lineage"][0][key] = value
    with pytest.raises(FactContractError, match="role|changed_flag"):
        _audit(material)


@pytest.mark.parametrize("key, value", [("start", 0), ("end", 0), ("before", "Z"), ("after", "다른말"),
    ("target_start", 0), ("target_end", 0), ("noun", ""), ("alias", ""), ("reason", "")])
def test_edit_span_binding_cannot_be_forged(material, key, value):
    material["lineage"][0]["edits"][0][key] = value
    with pytest.raises(FactContractError, match="edit_"):
        _audit(material)


def test_unlisted_edit_and_changed_numeric_fact_rejected():
    edit = {"start": 3, "end": 4, "before": "R", "after": "느티", "target_start": 3,
        "target_end": 5, "noun": "분말", "alias": "느티", "reason": "단위 시험 식별자 별칭"}
    with pytest.raises(FactContractError, match="unlisted_body_change"):
        revision.replay_edits("분말 R 15개", "분말 느티 16개", [edit])
    numeric = {**edit, "start": 5, "end": 7, "before": "15", "after": "16", "target_start": 5, "target_end": 7}
    with pytest.raises(FactContractError, match="numeric_facts_changed"):
        revision.replay_edits("분말 R 15개", "분말 R 16개", [numeric])


@pytest.mark.parametrize("key", ["family_id", "scenario_id", "template_family_id", "domain"])
def test_nontext_metadata_cannot_change(material, key):
    material["revised_records"][0][key] = "changed-value"
    with pytest.raises(FactContractError, match="family_changed|metadata_changed"):
        _audit(material)


def test_evidence_rationale_parent_draft_and_other_facts_are_preserved(material):
    material["revised_evidence"][0]["rationale"] += " 변경"
    material["lineage"][0]["child_evidence_sha256"] = value_digest(material["revised_evidence"][0])
    with pytest.raises(FactContractError, match="evidence_facts_changed"):
        _audit(material)


def test_answer_exclusions_preserved(material):
    answer = material["revised_answers"][0]
    answer["other_grade_exclusions"]["S1"] += " 변경"
    material["lineage"][0]["child_answer_sha256"] = value_digest(answer)
    with pytest.raises(FactContractError, match="answer_changed"):
        _audit(material)


def test_context_fact_change_fails_even_when_edited_body_is_valid(material):
    material["revised_records"][0]["input"]["context"][0]["value"] += "; extra: 예"
    with pytest.raises(FactContractError):
        _audit(material)


def test_conditional_grade_change_fails(material):
    material["revised_answers"][0]["reference_grade"] = "S1"
    with pytest.raises(FactContractError):
        _audit(material)


def test_nonpanel_body_or_metadata_changes_fail(material):
    material["revised_records"][2]["domain"] = "another-domain"
    with pytest.raises(FactContractError, match="nonpanel_record_changed"):
        _audit(material)


@pytest.mark.parametrize("mutation", ["wrong_pin", "missing_row", "changed_old_row", "missing_parent", "wrong_groups", "wrong_parent_pin"])
def test_old_inventory_and_group_contract_are_pinned(material, mutation):
    if mutation == "wrong_pin":
        material["expected_ledger_sha256"] = "f"*64
    elif mutation == "missing_row":
        material["ledger"]["records"].pop()
    elif mutation == "changed_old_row":
        material["ledger"]["records"][0]["reason"] = "model_selection"
    elif mutation == "missing_parent":
        material["panel_parent_ids"][0] = "doc-"+"f"*24
    elif mutation == "wrong_groups":
        material["expected_previous_groups"] += 1
    else:
        material["parent_manifest_sha256"] = "f"*64
    with pytest.raises(FactContractError):
        _audit(material)


def test_semantic_links_reference_old_ids_and_cannot_add_unknown_members(material):
    material["semantic_links"][0]["members"].append("doc-"+"f"*24)
    with pytest.raises(FactContractError, match="semantic_members"):
        _audit(material)


def _test_pack(root, members):
    root.mkdir()
    files = {}
    for name, values in members.items():
        content = ("".join(json.dumps(v, ensure_ascii=False)+"\n" for v in values)
                   if name.endswith(".jsonl") else cli._json(values))
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        files[name] = text_digest(content)
    manifest = cli._json({**FLAGS, "files": files})
    (root/"manifest.json").write_text(manifest, encoding="utf-8", newline="\n")
    return text_digest(manifest)


@pytest.fixture
def test_packs(material, tmp_path, monkeypatch):
    parent, child, integration = (tmp_path/name for name in ("parent", "child", "integration"))
    parent_sha = _test_pack(parent, {cli.COMMON[0]: material["original_records"], cli.COMMON[1]: material["original_answers"],
        cli.COMMON[2]: material["original_evidence"], "authoring/batch06_metadata.jsonl": [{"doc_id": k} for k in material["panel_parent_ids"]]})
    child_sha = _test_pack(child, {cli.COMMON[0]: material["revised_records"], cli.COMMON[1]: material["revised_answers"],
        cli.COMMON[2]: material["revised_evidence"], "audit/lineage.jsonl": material["lineage"]})
    integration_sha = _test_pack(integration, {"exposure/development260.json": material["ledger"],
        "exposure/semantic_links.json": material["semantic_links"]})
    monkeypatch.setattr(cli, "PARENT_MANIFEST_SHA256", parent_sha)
    monkeypatch.setattr(revision, "PARENT_MANIFEST_SHA256", parent_sha)
    monkeypatch.setattr(cli, "PRIOR_LEDGER_SHA256", material["expected_ledger_sha256"])
    monkeypatch.setattr(cli, "PRIOR_INTEGRATION_MANIFEST_SHA256", integration_sha)
    def small(*args, **kwargs):
        return revision.audit_revision(*args, **kwargs, expected_document_count=4, expected_panel_count=2,
                                       expected_previous_groups=material["expected_previous_groups"])
    monkeypatch.setattr(cli, "audit_revision", small)
    return {"original_pack": parent, "revision_pack": child, "expected_revision_manifest_sha256": child_sha,
        "prior_integration_pack": integration, "expected_original_manifest_sha256": parent_sha,
        "expected_integration_manifest_sha256": integration_sha}


def test_cli_prepare_pinned_sources_completion_and_no_overwrite(test_packs, tmp_path):
    out = tmp_path/"new-audit"
    result = cli.prepare(out, **test_packs)
    assert result["active_documents"] == 4 and result["physical_exposure_records_as_document_versions"] == 5
    manifest = json.loads((out/"manifest.json").read_text(encoding="utf-8"))
    assert set(manifest["files"]) == {"result.json", "summary.json"}
    assert set(manifest["source_files_sha256"]) == {"src/koipa/"+name for name in revision.source_hashes()} | {
        "scripts/audit_customer_revision_lineage_v1.py"}
    for name, sha in manifest["files"].items():
        assert hashlib.sha256((out/name).read_bytes()).hexdigest() == sha
    assert all(manifest[k] is False for k in FLAGS)
    with pytest.raises(FactContractError, match="output_exists"):
        cli.prepare(out, **test_packs)


@pytest.mark.parametrize("case", ["parent", "child", "integration", "alias", "unrelated_frozen"])
def test_cli_output_may_not_mutate_source_or_frozen_packs(test_packs, tmp_path, case):
    if case in {"parent", "child", "integration"}:
        key = {"parent": "original_pack", "child": "revision_pack", "integration": "prior_integration_pack"}[case]
        target = test_packs[key]/"new-child"
    elif case == "alias":
        source = test_packs["original_pack"]
        target = source/".."/source.name/"new-child"
    else:
        root = tmp_path/"other-frozen"
        root.mkdir()
        (root/"manifest.json").write_text("{}", encoding="utf-8")
        target = root/"deep"/"new-child"
    with pytest.raises(FactContractError, match="output_inside_source_pack|output_inside_frozen_pack"):
        cli.prepare(target, **test_packs)
    assert not target.exists()


@pytest.mark.parametrize("case", ["wrong_revision_pin", "changed_member", "wrong_ledger", "flag_promoted"])
def test_cli_source_integrity_failures(test_packs, tmp_path, case):
    if case == "wrong_revision_pin":
        test_packs["expected_revision_manifest_sha256"] = "0"*64
    elif case == "changed_member":
        path = test_packs["revision_pack"]/"audit/lineage.jsonl"
        path.write_text(path.read_text(encoding="utf-8")+" ", encoding="utf-8")
    elif case == "wrong_ledger":
        path = test_packs["prior_integration_pack"]/"exposure/development260.json"
        path.write_text("{}", encoding="utf-8")
    else:
        path = test_packs["revision_pack"]/"manifest.json"
        manifest = json.loads(path.read_text(encoding="utf-8"))
        manifest["training_allowed"] = True
        path.write_text(cli._json(manifest), encoding="utf-8")
        test_packs["expected_revision_manifest_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(FactContractError):
        cli.prepare(tmp_path/"not-created", **test_packs)
    assert not (tmp_path/"not-created").exists()


def test_cli_late_source_change_never_gets_completion_manifest(test_packs, tmp_path, monkeypatch):
    out = tmp_path/"incomplete"
    original = cli._check_snapshot
    calls = []
    def check(snapshot):
        calls.append(snapshot)
        if len(calls) == 2:
            assert (out/"result.json").exists() and not (out/"manifest.json").exists()
            raise FactContractError("revision_source_changed")
        original(snapshot)
    monkeypatch.setattr(cli, "_check_snapshot", check)
    with pytest.raises(FactContractError, match="source_changed"):
        cli.prepare(out, **test_packs)
    assert len(calls) == 2 and not (out/"manifest.json").exists()


def test_pack_reader_empty_jsonl_is_failure(tmp_path):
    root = tmp_path/"empty-pack"
    sha = _test_pack(root, {"audit/lineage.jsonl": []})
    with pytest.raises(FactContractError, match="jsonl_empty_or_blank"):
        cli._pack(root, sha, ("audit/lineage.jsonl",), {})
