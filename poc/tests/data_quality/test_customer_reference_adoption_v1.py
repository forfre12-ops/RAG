"""Explicit review decisions and limits; unit fixtures are not real review acts."""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from customer_benchmark_drafts import build_drafts
import prepare_customer_reference_adoption_v1 as cli
import koipa.customer_reference_adoption_v1 as adoption
from koipa.customer_benchmark import FLAGS
from koipa.customer_guide_reference import POLICY_SHA256, build_case
from koipa.policy_facts import FactContractError, text_digest, value_digest


@pytest.fixture
def material():
    facts = {"public_exact_body": False, "obtainable_without_holder": False, "ordinary_access_difficult": True,
        "cost_krw": 30000000, "person_hours": 600, "economic_utility": True, "investment_scope_exact": True,
        "secrecy_manageable": True, "all_staff_knows": False, "business_need_only": True,
        "individual_approval": True, "access_enforced": True, "release_authorized": False, "other_risk_present": False}
    triples = [build_case(d, facts, "단위 시험에 한하여 해당 정보의 직접 귀속 투입과 열람 상태를 가정한 것이며 실제 고객 관측이나 검토를 생성하지 않습니다.")
               for d in build_drafts()[:4]]
    decisions = []
    for index, (record, answer, evidence) in enumerate(triples):
        choice = ("keep", "accept") if index < 2 else ("revise", "hold") if index == 2 else ("exclude", "reject")
        benchmark = "eligible" if index == 1 else "hold"
        findings = [] if index == 1 else [{"code": "fixture-unresolved-scope", "scope": "benchmark" if index == 0 else "both",
            "reason": "단위 시험의 미해결 범위 문제를 나타내는 것으로 실제 문서 결함 판정이나 고객 평가 결과가 아닙니다."}]
        decisions.append({**FLAGS, "schema_version": adoption.DECISION_SCHEMA,
            "source_manifest_sha256": adoption.SOURCE_MANIFEST_SHA256, "doc_id": record["input"]["doc_id"],
            "input_sha256": record["input_sha256"], "body_sha256": text_digest(record["input"]["text"]),
            "answer_sha256": value_digest(answer), "evidence_sha256": value_digest(evidence),
            "policy_sha256": POLICY_SHA256, "reference_grade": answer["reference_grade"],
            "source_disposition": choice[0], "conditional_reference_decision": choice[1], "benchmark_decision": benchmark,
            "reviewer_kind": "ai_internal_review", "review_scope": "body_and_synthetic_context", "source_unchanged": True,
            "body_review_note": "본문 검토를 선언하는 단위시험 예시로 적용 대상과 예외의 인용 바인딩을 확인할 뿐 실제 의미 검토를 수행했다는 인증은 아닙니다.",
            "context_review_note": "가상 맥락 검토를 선언하는 단위시험 예시로 비공개 여부와 정보 귀속 투입 및 관리 조건의 근거 필드를 따로 결합합니다.",
            "decision_reason": "이 판정은 단위시험용 명시적 결정 기록으로 기존 정책의 곱 계산 결과만 보고 자동 채택한 것이 아니며 실제 검토 결과로 사용하지 않습니다.",
            "required_action": "단위시험 검증 후 실제 검토 기록과 구별하여 보관합니다.",
            "body_evidence": [{k: c[k] for k in ("quote", "start", "end", "sha256")} for c in record["claims"][:2]],
            "context_evidence": [{"name": c["name"], "quote": c["value"], "sha256": value_digest(c["value"])}
                                 for c in record["input"]["context"][:3]], "findings": findings})
    return {"records": [r for r, _, _ in triples], "answers": [a for _, a, _ in triples],
        "evidence": [e for _, _, e in triples], "decisions": decisions,
        "source_manifest_sha256": adoption.SOURCE_MANIFEST_SHA256,
        "hard_hold_benchmark_ids": [triples[0][0]["input"]["doc_id"]], "expected_count": 4, "expected_hard_hold_count": 1}


def test_explicit_internal_acceptance_not_training_adoption(material):
    original = copy.deepcopy(material)
    result = adoption.build_adoption_ledger(**material)
    assert result["reviewed_documents"] == 4
    assert result["internal_reference_accepted"] == 2
    assert result["internal_reference_held"] == result["internal_reference_rejected"] == 1
    assert result["benchmark_eligible_candidates"] == 1 and result["benchmark_held_candidates"] == 3
    assert result["benchmark_training_adopted"] == result["benchmark_evaluation_adopted"] == 0
    assert all(result[k] is False for k in FLAGS)
    assert result["semantic_review_truth_certified"] is result["review_reading_claim_independently_verified"] is False
    assert result["source_document_flags_changed"] is result["model_forward_executed"] is False
    assert result["ledger_sha256"] == value_digest({k: v for k, v in result.items() if k != "ledger_sha256"})
    assert material == original
    assert adoption.verify_adoption_ledger(result, **material) == result


@pytest.mark.parametrize("case", ["empty", "missing", "duplicate", "foreign"])
def test_full_review_exact_coverage_required(material, case):
    if case == "empty":
        material["decisions"] = []
    elif case == "missing":
        material["decisions"].pop()
    elif case == "duplicate":
        material["decisions"][1] = copy.deepcopy(material["decisions"][0])
    else:
        material["decisions"][1]["doc_id"] = "doc-"+"f"*24
    with pytest.raises(FactContractError, match="coverage|review_ids"):
        adoption.build_adoption_ledger(**material)


@pytest.mark.parametrize("key", ["source_manifest_sha256", "input_sha256", "body_sha256", "answer_sha256",
    "evidence_sha256", "policy_sha256", "reference_grade"])
def test_decision_pin_binding_is_checked(material, key):
    material["decisions"][0][key] = "wrong"
    with pytest.raises(FactContractError, match="source_binding"):
        adoption.build_adoption_ledger(**material)


@pytest.mark.parametrize("key", list(FLAGS))
@pytest.mark.parametrize("value", [True, 0, None])
def test_permission_promotion_or_nonboolean_is_forbidden(material, key, value):
    material["decisions"][0][key] = value
    with pytest.raises(FactContractError, match="permission"):
        adoption.build_adoption_ledger(**material)


@pytest.mark.parametrize("key, value", [("reviewer_kind", "human_reviewer"), ("review_scope", "formula_check_only"),
    ("source_unchanged", False), ("source_unchanged", 1), ("schema_version", "gold-approved")])
def test_no_manufactured_human_or_formula_only_acceptance(material, key, value):
    material["decisions"][0][key] = value
    with pytest.raises(FactContractError, match="review_authority"):
        adoption.build_adoption_ledger(**material)


@pytest.mark.parametrize("key", ["body_review_note", "context_review_note", "decision_reason", "required_action"])
def test_substantive_written_review_required(material, key):
    material["decisions"][0][key] = "수치 검산 통과"
    with pytest.raises(FactContractError, match="note_required|action_missing"):
        adoption.build_adoption_ledger(**material)


def test_body_and_context_review_are_separate(material):
    material["decisions"][0]["context_review_note"] = material["decisions"][0]["body_review_note"]
    with pytest.raises(FactContractError, match="not_separated"):
        adoption.build_adoption_ledger(**material)


@pytest.mark.parametrize("case", ["one", "duplicate", "offset", "boolean_offset", "hash", "wrong_quote", "extra_field"])
def test_body_quote_evidence_is_bound(material, case):
    quotes = material["decisions"][0]["body_evidence"]
    if case == "one":
        quotes.pop()
    elif case == "duplicate":
        quotes[1] = copy.deepcopy(quotes[0])
    elif case == "offset":
        quotes[0]["start"] += 1
    elif case == "boolean_offset":
        quotes[0]["start"] = True
    elif case == "hash":
        quotes[0]["sha256"] = "f"*64
    elif case == "wrong_quote":
        quotes[0]["quote"] = "문서에 없는 인용문"
    else:
        quotes[0]["approval"] = True
    with pytest.raises(FactContractError, match="body_"):
        adoption.build_adoption_ledger(**material)


@pytest.mark.parametrize("case", ["missing", "duplicate", "wrong_value", "wrong_hash", "skip_management"])
def test_core_context_must_be_explicitly_bound(material, case):
    quotes = material["decisions"][0]["context_evidence"]
    if case == "missing":
        quotes.pop()
    elif case == "duplicate":
        quotes[1] = copy.deepcopy(quotes[0])
    elif case == "wrong_value":
        quotes[0]["quote"] += " 조작"
    elif case == "wrong_hash":
        quotes[0]["sha256"] = "f"*64
    else:
        value = material["records"][0]["input"]["context"][3]
        quotes[2] = {"name": value["name"], "quote": value["value"], "sha256": value_digest(value["value"])}
    with pytest.raises(FactContractError, match="context_"):
        adoption.build_adoption_ledger(**material)


@pytest.mark.parametrize("source, conditional", [("keep", "hold"), ("revise", "accept"), ("exclude", "accept"),
    ("keep", "reject"), ("unknown", "accept")])
def test_source_and_conditional_decisions_cannot_conflict(material, source, conditional):
    material["decisions"][0].update(source_disposition=source, conditional_reference_decision=conditional)
    with pytest.raises(FactContractError, match="decisions_conflict"):
        adoption.build_adoption_ledger(**material)


def test_hard_benchmark_hold_cannot_be_lifted_by_review(material):
    material["decisions"][0].update(benchmark_decision="eligible", findings=[])
    with pytest.raises(FactContractError, match="existing_benchmark_hold_bypassed"):
        adoption.build_adoption_ledger(**material)


@pytest.mark.parametrize("case", ["accept_reference_finding", "eligible_benchmark_finding", "hold_without_finding", "benchmark_hold_without_finding"])
def test_unresolved_findings_and_disposition_consistency(material, case):
    if case == "accept_reference_finding":
        material["decisions"][0]["findings"][0]["scope"] = "both"
        error = "accept_with_open_reference"
    elif case == "eligible_benchmark_finding":
        material["decisions"][1]["findings"] = copy.deepcopy(material["decisions"][0]["findings"])
        error = "eligible_with_open_benchmark"
    elif case == "hold_without_finding":
        material["decisions"][2]["findings"] = []
        error = "unresolved_reference_finding"
    else:
        material["decisions"][0]["findings"] = []
        error = "benchmark_hold_finding"
    with pytest.raises(FactContractError, match=error):
        adoption.build_adoption_ledger(**material)


def test_reference_hold_never_becomes_benchmark_eligible(material):
    material["decisions"][2]["benchmark_decision"] = "eligible"
    with pytest.raises(FactContractError, match="benchmark_reference_conflict"):
        adoption.build_adoption_ledger(**material)


@pytest.mark.parametrize("case", ["count", "hardhold", "sourcepin", "forge_ledger"])
def test_ledger_contract_and_replay(material, case):
    if case == "count":
        material["expected_count"] = 5
    elif case == "hardhold":
        material["hard_hold_benchmark_ids"] = ["doc-"+"f"*24]
    elif case == "sourcepin":
        material["source_manifest_sha256"] = "f"*64
    else:
        ledger = adoption.build_adoption_ledger(**material)
        ledger["benchmark_training_adopted"] = 1
        ledger["ledger_sha256"] = value_digest({k: v for k, v in ledger.items() if k != "ledger_sha256"})
        with pytest.raises(FactContractError, match="ledger_replay"):
            adoption.verify_adoption_ledger(ledger, **material)
        return
    with pytest.raises(FactContractError):
        adoption.build_adoption_ledger(**material)


@pytest.fixture
def files(material, tmp_path, monkeypatch):
    root = tmp_path/"source-pack"
    root.mkdir()
    manifest_files = {}
    values = [material["records"], material["answers"], material["evidence"],
              [{"doc_id": i} for i in material["hard_hold_benchmark_ids"]]]
    for name, rows in zip(cli.SOURCE_MEMBERS, values, strict=True):
        path = root/name
        path.parent.mkdir(parents=True, exist_ok=True)
        content = "".join(json.dumps(r, ensure_ascii=False)+"\n" for r in rows)
        path.write_text(content, encoding="utf-8", newline="\n")
        manifest_files[name] = text_digest(content)
    content = cli._json({**FLAGS, "files": manifest_files})
    (root/"manifest.json").write_text(content, encoding="utf-8", newline="\n")
    source_pin = text_digest(content)
    monkeypatch.setattr(cli, "SOURCE_MANIFEST_SHA256", source_pin)
    monkeypatch.setattr(adoption, "SOURCE_MANIFEST_SHA256", source_pin)
    rows = copy.deepcopy(material["decisions"])
    for row in rows:
        row["source_manifest_sha256"] = source_pin
    review_files, hashes = [], []
    for index, part in enumerate((rows[:2], rows[2:])):
        path = tmp_path/f"review-{index}.jsonl"
        text = "".join(json.dumps(r, ensure_ascii=False)+"\n" for r in part)
        path.write_text(text, encoding="utf-8", newline="\n")
        review_files.append(path)
        hashes.append(text_digest(text))
    def small(*args, **kwargs):
        return adoption.build_adoption_ledger(*args, **kwargs, expected_count=4, expected_hard_hold_count=1)
    monkeypatch.setattr(cli, "build_adoption_ledger", small)
    return {"source_pack": root, "review_files": review_files, "expected_review_sha256": hashes}


def test_cli_separate_ledger_and_last_manifest(files, tmp_path):
    out = tmp_path/"adoption-report"
    result = cli.prepare(out, **files)
    assert result["internal_reference_accepted"] == 2 and result["benchmark_training_adopted"] == 0
    manifest = json.loads((out/"manifest.json").read_text(encoding="utf-8"))
    assert len(manifest["review_sources"]) == 2
    assert "scripts/prepare_customer_reference_adoption_v1.py" in manifest["source_files_sha256"]
    assert all(manifest[k] is False for k in FLAGS)
    for name, sha in manifest["files"].items():
        assert hashlib.sha256((out/name).read_bytes()).hexdigest() == sha
    with pytest.raises(FactContractError, match="output_exists"):
        cli.prepare(out, **files)


@pytest.mark.parametrize("case", ["source_child", "source_alias", "frozen_child", "review_file"])
def test_cli_output_guards(files, tmp_path, case):
    if case == "source_child":
        out = files["source_pack"]/"new"
    elif case == "source_alias":
        root = files["source_pack"]
        out = root/".."/root.name/"new"
    elif case == "frozen_child":
        frozen = tmp_path/"other-pack"
        frozen.mkdir()
        (frozen/"manifest.json").write_text("{}", encoding="utf-8")
        out = frozen/"nested"/"new"
    else:
        out = files["review_files"][0]
    with pytest.raises(FactContractError, match="output_"):
        cli.prepare(out, **files)


@pytest.mark.parametrize("case", ["changed_source", "bad_review_pin", "duplicate_file", "empty_review"])
def test_cli_input_pins_fail_closed(files, tmp_path, case):
    if case == "changed_source":
        path = files["source_pack"]/cli.SOURCE_MEMBERS[0]
        path.write_text(path.read_text(encoding="utf-8")+" ", encoding="utf-8")
    elif case == "bad_review_pin":
        files["expected_review_sha256"][0] = "f"*64
    elif case == "duplicate_file":
        files["review_files"][1] = files["review_files"][0]
    else:
        files["review_files"][0].write_text("", encoding="utf-8")
        files["expected_review_sha256"][0] = text_digest("")
    with pytest.raises(FactContractError):
        cli.prepare(tmp_path/"not-created", **files)
    assert not (tmp_path/"not-created").exists()


def test_cli_late_failure_has_no_completion_manifest(files, tmp_path, monkeypatch):
    original = cli._unchanged
    out = tmp_path/"incomplete"
    calls = []
    def check(snapshots):
        calls.append(snapshots)
        assert not (out/"manifest.json").exists()
        if len(calls) == 2:
            assert (out/"ledger.json").exists()
            raise FactContractError("adoption_input_or_code_changed")
        original(snapshots)
    monkeypatch.setattr(cli, "_unchanged", check)
    with pytest.raises(FactContractError, match="input_or_code_changed"):
        cli.prepare(out, **files)
    assert len(calls) == 2 and not (out/"manifest.json").exists()
