"""Alias scope, exact outside-span preservation and non-promoting pack regression."""
from __future__ import annotations

import copy
import inspect
import json
import re
import shutil
from collections import Counter
from decimal import Decimal

import pytest

import build_customer_guide_alias_repair_v1 as batch
import customer_guide_alias_repair_v1 as repair
from koipa.dataset_usage import assert_dataset_usage
from koipa.policy_facts import FactContractError, text_digest, value_digest


@pytest.fixture(scope="module")
def material():
    return batch.core_payload()


@pytest.fixture(scope="module")
def originals():
    docs, answers, payload = batch.parent.core_payload()
    return ({d.input.doc_id: d.model_dump() for d in docs}, {a["doc_id"]: a for a in answers},
            {e["doc_id"]: e for e in batch._rows(payload["answers/evidence.jsonl"].encode())})


@pytest.fixture(scope="module")
def packet(tmp_path_factory):
    root = tmp_path_factory.mktemp("alias-repair")/"pack"
    batch.prepare(root)
    return root


def test_active260_no_new_docs_and_all_scope_bounds(material):
    docs, answers, payload = material
    summary = json.loads(payload["summary.json"])
    assert len(docs) == len(answers) == len({d.input.text for d in docs}) == 260
    assert Counter(a["reference_grade"] for a in answers) == {"TS": 54, "S1": 72, "S2": 65, "S3": 69}
    assert summary["new_benchmark_documents"] == summary["new_bodies"] == 0
    assert summary["changed_views"] == 16 and summary["unchanged_panel_documents"] == 48
    assert summary["outside_panel_preserved"] == 196 and summary["unchanged_previous_bodies"] == 244
    assert summary["edited_alias_spans"] == 18 and summary["preserved_single_latin_unit_spans"] == 10
    assert summary["quote_bound_claims"] == 520 and summary["context_fact_bindings"] == 3640
    assert summary["arithmetic_checks"] == 207 and summary["repaired_panel_arithmetic_checks"] == 64
    assert sum(summary["remaining_before_rejections"].values()) == 740
    assert summary["source_hold_automatically_cleared"] is summary["whole_semantic_equivalence_certified"] is False
    assert summary["authoring_quality_status"] == "AUTHORING_QUALITY_HOLD_TARGET_BINDING"
    assert summary["diagnostic_view_only"] is summary["target_binding_not_certified"] is True
    assert summary["adoption_allowed"] is summary["authoritative_parent_replaced"] is False
    assert summary["accepted_train"] == summary["accepted_evaluation"] == summary["body_only_grade_eligible"] == 0
    assert value_digest(batch.POLICY) == "e3aa2854120397342574c2487a46c83a4b74b9108c706bbc3c4b47c8dfbe1ac9"
    assert list(inspect.signature(repair.transform).parameters) == ["family_id", "text"]


def test_original196_and_unchanged48_exact_records_answers_evidence(material, originals):
    docs, answers, payload = material
    old_records, old_answers, old_details = originals
    records = {d.input.doc_id: d.model_dump() for d in docs}
    answers = {a["doc_id"]: a for a in answers}
    details = {e["doc_id"]: e for e in batch._rows(payload["answers/evidence.jsonl"].encode())}
    lineage = batch._rows(payload["audit/lineage.jsonl"].encode())
    changed = {r["parent_doc_id"] for r in lineage if r["changed"]}
    for doc_id in set(old_records)-changed:
        assert records[doc_id] == old_records[doc_id]
        assert answers[doc_id] == old_answers[doc_id]
        assert details[doc_id] == old_details[doc_id]
    assert len(set(old_records)-changed) == 244
    assert not changed & records.keys()
    assert {r["input"]["doc_id"] for r in batch._rows(payload["parents/documents.jsonl"].encode())} == {r["parent_doc_id"] for r in lineage}


@pytest.mark.parametrize("index", range(64))
def test_each_view_literal_replay_nonedit_bytes_conditions_and_bindings(material, originals, index):
    docs, answers, payload = material
    line = batch._rows(payload["audit/lineage.jsonl"].encode())[index]
    parent = originals[0][line["parent_doc_id"]]
    child = next(d.model_dump() for d in docs if d.input.doc_id == line["child_doc_id"])
    answer = next(a for a in answers if a["doc_id"] == line["child_doc_id"])
    detail = next(e for e in batch._rows(payload["answers/evidence.jsonl"].encode()) if e["doc_id"] == line["child_doc_id"])
    source, target = parent["input"]["text"], child["input"]["text"]
    source_cursor, target_cursor, pieces = 0, 0, []
    for edit in line["edits"]:
        assert source[edit["start"]:edit["end"]] == edit["before"]
        assert target[edit["target_start"]:edit["target_end"]] == edit["after"]
        before_segment = source[source_cursor:edit["start"]]
        after_segment = target[target_cursor:edit["target_start"]]
        assert before_segment.encode() == after_segment.encode()
        pieces.extend([before_segment, edit["after"]])
        source_cursor, target_cursor = edit["end"], edit["target_end"]
    assert source[source_cursor:].encode() == target[target_cursor:].encode()
    pieces.append(source[source_cursor:])
    assert "".join(pieces) == target
    assert re.findall(r"\d+(?:\.\d+)?", source) == re.findall(r"\d+(?:\.\d+)?", target)
    assert re.findall(r"않\w*|못\w*|없\w*|아니\w*|때만|전에는|확인한 뒤", source) == re.findall(r"않\w*|못\w*|없\w*|아니\w*|때만|전에는|확인한 뒤", target)
    assert source.splitlines()[0] == target.splitlines()[0]
    assert parent["input"]["context"] == child["input"]["context"]
    for name in ("family_id", "scenario_id", "template_family_id"):
        assert parent[name] == child[name] == line[name]
    for old_claim, new_claim in zip(parent["claims"], child["claims"], strict=True):
        assert {k: v for k, v in old_claim.items() if k not in {"start", "end", "quote", "claim", "sha256"}} == {
            k: v for k, v in new_claim.items() if k not in {"start", "end", "quote", "claim", "sha256"}}
        assert target[new_claim["start"]:new_claim["end"]] == new_claim["quote"] == new_claim["claim"]
        assert text_digest(new_claim["quote"]) == new_claim["sha256"]
    old_answer, old_detail = originals[1][line["parent_doc_id"]], originals[2][line["parent_doc_id"]]
    assert {k: v for k, v in answer.items() if k not in {"doc_id", "input_sha256"}} == {
        k: v for k, v in old_answer.items() if k not in {"doc_id", "input_sha256"}}
    assert {k: v for k, v in detail.items() if k not in {"doc_id", "input_sha256", "body_sha256"}} == {
        k: v for k, v in old_detail.items() if k not in {"doc_id", "input_sha256", "body_sha256"}}
    assert value_digest(parent["input"]) == line["parent_input_sha256"]
    assert value_digest(child["input"]) == line["child_input_sha256"]
    assert value_digest(old_answer) == line["parent_answer_sha256"]
    assert value_digest(answer) == line["child_answer_sha256"]
    assert value_digest(old_detail) == line["parent_evidence_sha256"]
    assert value_digest(detail) == line["child_evidence_sha256"]
    assert all(line[k] is False for k in batch.FLAGS)
    assert line["new_document_count"] == 0 and line["repair_kind"] == "identifier_alias_repair"
    assert len(line) == 24
    assert line["changed"] == (source != target) == (line["parent_doc_id"] != line["child_doc_id"])


@pytest.mark.parametrize("index", range(64))
def test_each_numeric_result_still_fails_after_mutation(material, index):
    docs = list(material[0])
    spec = batch.parent.CASES[index]
    pos = next(i for i, d in enumerate(docs) if d.family_id == "family-"+spec["key"])
    d = docs[pos]
    match = re.search(spec["pattern"], d.input.text)
    start, end = match.span(match.lastindex)
    text = d.input.text[:start]+str(Decimal(match.group(match.lastindex))+1)+d.input.text[end:]
    docs[pos] = d.model_copy(update={"input": d.input.model_copy(update={"text": text})})
    with pytest.raises(FactContractError, match="arithmetic_mismatch"):
        batch.parent.arithmetic(docs)


def test_particle_outcomes_distinct_nouns_and_units(originals):
    changes = {(e["before"], e["after"]) for r in repair.REVIEWED_PARENTS.values() for e in r["edits"]}
    assert {("분할 K는", "분할은"), ("구역 D는", "구역은"), ("분말 W가", "분말이"),
            ("저장 경로 M은", "저장 경로는"), ("효소 E는", "효소는"),
            ("기질 배치 M에", "기질 배치에")} <= changes
    droplet = next(r for r in originals[0].values() if r["family_id"] == "family-droplet-hysteresis")
    result = repair.transform(droplet["family_id"], droplet["input"]["text"])
    assert result["text"].count("2μL") == 1
    assert result["text"].count("표면") == droplet["input"]["text"].count("표면")
    enzyme = next(r for r in originals[0].values() if r["family_id"] == "family-enzyme-addition-lag")
    result = repair.transform(enzyme["family_id"], enzyme["input"]["text"])
    assert "효소는" in result["text"] and "기질 배치에 한정" in result["text"]
    assert "다른 농도의 반응 속도로 그대로 환산하지 않습니다" in result["text"]
    assert "\ufffd" not in json.dumps(repair.REVIEWED_PARENTS, ensure_ascii=False)


@pytest.mark.parametrize("text", ["시편 A는 남기고 시편 B는 제외한다.", "분말 A와 다른 분말 B를 비교한다."])
def test_compared_targets_are_not_collapsed(text):
    with pytest.raises(FactContractError, match="multiple_targets_ambiguous"):
        repair.reject_ambiguous_targets(text)


@pytest.mark.parametrize("mutation", ["unknown_parent", "unknown_body", "unknown_alias", "wrong_after", "unit_removed", "bad_span"])
def test_unreviewed_or_noncosmetic_mutation_fails(originals, monkeypatch, mutation):
    original = next(r for r in originals[0].values() if r["family_id"] == "family-droplet-hysteresis")
    family, text = original["family_id"], original["input"]["text"]
    registry = copy.deepcopy(repair.REVIEWED_PARENTS)
    if mutation == "unknown_parent":
        family = "family-not-reviewed"
    elif mutation == "unknown_body":
        text += "조건 변경"
    elif mutation == "unknown_alias":
        text += "\n추가 표식 Z를 기록했다."
        registry[family]["body_sha256"] = text_digest(text)
    elif mutation == "wrong_after":
        registry[family]["edits"][0]["after"] = "해당 표면에"
    elif mutation == "unit_removed":
        registry[family]["preserved_single_latin"] = []
    else:
        registry[family]["edits"][0]["start"] += 1
    monkeypatch.setattr(repair, "REVIEWED_PARENTS", registry)
    with pytest.raises(FactContractError):
        repair.transform(family, text)


def test_claim_boundary_inside_edit_is_rejected(originals):
    original = next(r for r in originals[0].values() if r["family_id"] == "family-droplet-hysteresis")
    source = original["input"]["text"]
    variant = repair.transform(original["family_id"], source)
    start, end = variant["edits"][0]["start"]+1, variant["edits"][0]["end"]
    quote = source[start:end]
    with pytest.raises(FactContractError, match="claim_cuts_edit"):
        repair.rebind_claims([{"start": start, "end": end, "quote": quote, "claim": quote}], source, variant)


@pytest.mark.parametrize("purpose", ["training", "model_evaluation"])
def test_no_implicit_release(material, purpose):
    with pytest.raises(ValueError):
        assert_dataset_usage([d.model_dump() for d in material[0]], purpose=purpose)


def test_prepare_verify_cli_and_nonoverwrite(packet, tmp_path):
    assert batch.verify(packet)["unique_bodies"] == 260
    with pytest.raises(FactContractError, match="output_exists"):
        batch.prepare(packet)
    assert batch.main(["verify", "--pack", str(packet)]) == 0
    assert batch.main(["verify", "--pack", str(tmp_path/"missing")]) == 2
    with pytest.raises(SystemExit):
        batch.main(["prepare", "--out", str(tmp_path/"new")])


@pytest.mark.parametrize("mutation", ["lineage", "answer", "context", "registry", "unit", "source", "flags", "adoption", "parent_replaced", "path", "extra", "missing", "token"])
def test_rehashed_tampering_is_rejected(packet, tmp_path, mutation):
    root = tmp_path/"copy"
    shutil.copytree(packet, root)
    manifest_path = root/"manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    path = None
    if mutation == "lineage":
        path = "audit/lineage.jsonl"
        rows = batch._rows((root/path).read_bytes())
        rows[0]["child_doc_id"] = rows[1]["child_doc_id"]
        content = batch._jsonl(rows)
    elif mutation == "answer":
        path = "answers/answers.candidate.jsonl"
        rows = batch._rows((root/path).read_bytes())
        rows[0]["reference_grade"] = "TS" if rows[0]["reference_grade"] != "TS" else "S3"
        content = batch._jsonl(rows)
    elif mutation == "context":
        path = "inputs/body_context.jsonl"
        rows = batch._rows((root/path).read_bytes())
        rows[0]["context"] = []
        content = batch._jsonl(rows)
    elif mutation == "registry":
        path, content = "audit/reviewed_aliases.json", "{}"
    elif mutation == "unit":
        path = "authoring/documents.jsonl"
        rows = batch._rows((root/path).read_bytes())
        row = next(r for r in rows if r["family_id"] == "family-droplet-hysteresis")
        row["input"]["text"] = row["input"]["text"].replace("2μL", "2")
        content = batch._jsonl(rows)
    elif mutation == "source":
        manifest["source_files_sha256"] = {}
    elif mutation == "flags":
        manifest["training_allowed"] = 0
    elif mutation == "adoption":
        manifest["adoption_allowed"] = True
    elif mutation == "parent_replaced":
        manifest["authoritative_parent_replaced"] = True
    elif mutation == "path":
        manifest["files"]["../escape"] = "0"*64
    elif mutation == "extra":
        (root/"extra").write_text("extra", encoding="utf-8")
    elif mutation == "missing":
        del manifest["files"]["summary.json"]
    else:
        path, content = "audit/tokenizer.json", batch._json({"status": "not_run", "model_inference_performed": True})
    if path:
        (root/path).write_text(content, encoding="utf-8", newline="\n")
        manifest["files"][path] = text_digest(content)
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises((ValueError, OSError)):
        batch.verify(root)


def test_output_rejects_frozen_ancestor_and_tokenizer_directory(packet, tmp_path):
    with pytest.raises(FactContractError, match="output_inside_frozen_pack"):
        batch.prepare(packet/"nested"/"new")
    model = tmp_path/"model"
    model.mkdir()
    with pytest.raises(FactContractError, match="output_inside_tokenizer_directory"):
        batch.prepare(model/"out", tokenizer=model/"tokenizer.json")
    assert not (packet/"nested").exists() and not (model/"out").exists()


def test_parent_hash_mismatch_blocks_output(tmp_path, monkeypatch):
    parent = tmp_path/"parent"
    parent.mkdir()
    (parent/"manifest.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(batch.parent, "verify", lambda _: {})
    with pytest.raises(FactContractError, match="parent_manifest_mismatch"):
        batch.prepare(tmp_path/"out", parent_pack=parent)
    assert not (tmp_path/"out").exists()


@pytest.mark.parametrize("phase", ["scan", "write"])
def test_parent_drift_cannot_complete_pack(tmp_path, monkeypatch, phase):
    parent = tmp_path/"parent"
    parent.mkdir()
    path = parent/"manifest.json"
    path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(batch.parent, "verify", lambda _: {})
    monkeypatch.setattr(batch, "PARENT_MANIFEST", batch.hashlib.sha256(path.read_bytes()).hexdigest())
    if phase == "scan":
        def scan(*_):
            path.write_text('{"changed":true}', encoding="utf-8")
            return {"status": "not_run", "model_inference_performed": False}
        monkeypatch.setattr(batch, "token_audit", scan)
    else:
        actual_write = batch._new_file
        def write(output, content):
            actual_write(output, content)
            path.write_text('{"changed":true}', encoding="utf-8")
        monkeypatch.setattr(batch, "_new_file", write)
    with pytest.raises(FactContractError, match="parent_manifest_mismatch"):
        batch.prepare(tmp_path/"out", parent_pack=parent)
    assert not (tmp_path/"out"/"manifest.json").exists()
    if phase == "scan":
        assert not (tmp_path/"out").exists()


@pytest.mark.parametrize("change_call", [2, 3])
def test_source_drift_never_binds_old_payload_to_new_source(tmp_path, monkeypatch, change_call):
    captured = batch.source_hashes()
    calls = 0
    def hashes():
        nonlocal calls
        calls += 1
        return captured if calls < change_call else {**captured, "scripts/changed.py": "0"*64}
    monkeypatch.setattr(batch, "source_hashes", hashes)
    with pytest.raises(FactContractError, match="source_changed_during_build"):
        batch.prepare(tmp_path/"out")
    assert not (tmp_path/"out"/"manifest.json").exists()
    if change_call == 2:
        assert not (tmp_path/"out").exists()
