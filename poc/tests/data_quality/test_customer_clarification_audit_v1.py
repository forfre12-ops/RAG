"""Small source-version contracts; unit fixtures are not real semantic reviews."""
from __future__ import annotations

import copy

import pytest

from customer_benchmark_drafts import build_drafts
import koipa.customer_clarification_audit_v1 as clarification
from koipa.customer_benchmark import FLAGS, validate_documents
from koipa.customer_eval_partition_v1 import audit_exposure, build_exposure_ledger, extend_exposure_ledger
from koipa.customer_guide_reference import POLICY_SHA256, build_case
from koipa.policy_facts import FactContractError, text_digest, value_digest


def _rebind(record):
    record["input"]["doc_id"] = "doc-"+value_digest({"text": record["input"]["text"], "context": record["input"]["context"]})[:24]
    record["input_sha256"] = value_digest(record["input"])


def _revision(triple, facts, index):
    parent, old_answer, old_evidence = triple
    child = copy.deepcopy(parent)
    quote = f"관측 범위는 같은 날짜의 서로 다른 {index+2}건으로 한정하며 재전송과 취소 항목은 이 판본의 집계에서 제외한다."
    start = len(child["input"]["text"])+1
    child["input"]["text"] += "\n"+quote
    claim = {"name": "clarified-scope", "claim": quote, "quote": quote, "start": start,
             "end": start+len(quote), "sha256": text_digest(quote), "status": "authored_binding_only"}
    child["claims"].append(claim)
    _rebind(child)
    rationale = "이 판본은 단위 검증을 위한 가상 범위 명시 사례이며 실제 문서를 읽은 의미 검토나 독립적인 고객 정답 판단으로 사용하지 않습니다."
    child, answer, evidence = build_case(child, facts, rationale)
    evidence["parent_draft_id"] = old_evidence["parent_draft_id"]
    parent_id, child_id = parent["input"]["doc_id"], child["input"]["doc_id"]
    edit = {"start": start-1, "end": start-1, "before": "", "after": "\n"+quote,
            "target_start": start-1, "target_end": start+len(quote), "before_sha256": text_digest(""), "after_sha256": text_digest("\n"+quote)}
    scope = {"statement": "새 합성 판본에만 추가한 동시점 관측 범위 가정입니다.", "origin": "authored_for_revised_synthetic_edition",
             "quote": quote, "start": start, "end": start+len(quote), "sha256": text_digest(quote)}
    row = {**FLAGS, **{k: parent[k] for k in clarification.FAMILIES}, "parent_doc_id": parent_id, "child_doc_id": child_id,
        "parent_input_sha256": parent["input_sha256"], "child_input_sha256": child["input_sha256"],
        "parent_body_sha256": text_digest(parent["input"]["text"]), "child_body_sha256": text_digest(child["input"]["text"]),
        "parent_answer_sha256": value_digest(old_answer), "child_answer_sha256": value_digest(answer),
        "parent_evidence_sha256": value_digest(old_evidence), "child_evidence_sha256": value_digest(evidence),
        "policy_sha256": POLICY_SHA256, "context_sha256": value_digest(parent["input"]["context"]),
        "repair_kind": "synthetic_scope_clarification", "new_document_count": 0, "edits": [edit],
        "additional_assumptions": [scope], "excluded_scope": [copy.deepcopy(scope)],
        "claim_changes": [{"name": claim["name"], "before": None, "after": claim}],
        "rationale_change": {"before": old_evidence["rationale"], "after": rationale}, "remaining_hold": bool(index)}
    note = {**FLAGS, "parent_doc_id": parent_id, "child_doc_id": child_id,
        "review_kind": "ai_authored_clarification_not_independent_approval", "decision_reason": rationale,
        "remaining_issues": ["단위 시험에서 의도적으로 남겨 둔 대상 범위 미확정 문제입니다."] if index else [],
        "remaining_hold": bool(index)}
    return (child, answer, evidence), row, note


@pytest.fixture
def material(monkeypatch):
    facts = {"public_exact_body": False, "obtainable_without_holder": False, "ordinary_access_difficult": True,
        "cost_krw": 30000000, "person_hours": 600, "economic_utility": True, "investment_scope_exact": True,
        "secrecy_manageable": True, "all_staff_knows": False, "business_need_only": True,
        "individual_approval": True, "access_enforced": True, "release_authorized": False, "other_risk_present": False}
    triples = [build_case(copy.deepcopy(draft), facts,
        "원본 역할을 하는 가상 단위 시험 사례이며 범위와 관리 전제를 고정한 검증 데이터일 뿐 고객 관측이나 실제 문서 검수 결과는 아닙니다.")
        for draft in build_drafts()[:4]]
    original = [t[0] for t in triples]
    answers, evidence = [t[1] for t in triples], [t[2] for t in triples]
    old_extra = copy.deepcopy(original[2])
    old_extra["input"]["text"] += "\n이전 진단 전용 판본이며 현재 정본에 포함하지 않는다."
    _rebind(old_extra)
    ledger = build_exposure_ledger(original, source_ref="unit-source", source_sha256="1"*64, reason="development_authoring")
    ledger = extend_exposure_ledger(ledger, [old_extra], expected_ledger_sha256=ledger["ledger_sha256"],
        source_ref="unit-prior-version", source_sha256="2"*64, reason="development_authoring")
    links = [{"members": [original[0]["input"]["doc_id"], original[3]["input"]["doc_id"]],
              "reason": "원본 문서 계열 관계를 신규 판본에도 보존하는 단위 시험 연결입니다."}]
    prior = audit_exposure(original, ledger, expected_ledger_sha256=ledger["ledger_sha256"], semantic_links=links)
    adoption = {**FLAGS, "source_manifest_sha256": clarification.PARENT_MANIFEST_SHA256, "policy_sha256": POLICY_SHA256,
        "documents_sha256": value_digest([d.model_dump() for d in sorted(validate_documents(original), key=lambda d: d.input.doc_id)]),
        "answers_sha256": value_digest(sorted(answers, key=lambda r: r["doc_id"])),
        "evidence_sha256": value_digest(sorted(evidence, key=lambda r: r["doc_id"])),
        "decisions": [{"doc_id": r["input"]["doc_id"], "conditional_reference_decision": "hold" if i < 2 else "accept",
            "source_disposition": "revise" if i < 2 else "keep", "benchmark_decision": "hold"} for i, r in enumerate(original)],
        "internal_reference_held_ids": [r["input"]["doc_id"] for r in original[:2]],
        "internal_reference_accepted_ids": [r["input"]["doc_id"] for r in original[2:]]}
    adoption["ledger_sha256"] = value_digest(adoption)
    monkeypatch.setattr(clarification, "ADOPTION_LEDGER_SHA256", adoption["ledger_sha256"])
    monkeypatch.setattr(clarification, "EXPOSURE_LEDGER_SHA256", ledger["ledger_sha256"])
    changed = [_revision(triples[i], facts, i) for i in range(2)]
    active = [changed[0][0], changed[1][0], *copy.deepcopy(triples[2:])]
    return {"original_records": original, "original_answers": answers, "original_evidence": evidence,
        "revisions": {"records": [t[0] for t in active], "answers": [t[1] for t in active], "evidence": [t[2] for t in active],
                      "lineage": [r[1] for r in changed], "review_notes": [r[2] for r in changed]},
        "adoption_ledger": adoption, "parent_manifest_sha256": clarification.PARENT_MANIFEST_SHA256,
        "adoption_manifest_sha256": clarification.ADOPTION_MANIFEST_SHA256, "expected_adoption_ledger_sha256": adoption["ledger_sha256"],
        "ledger": ledger, "expected_ledger_sha256": ledger["ledger_sha256"], "source_ref": "unit-clarification-candidate",
        "source_sha256": "3"*64, "semantic_links": links, "expected_document_count": 4,
        "expected_revision_count": 2, "expected_previous_versions": 5, "expected_previous_groups": len(prior["groups"])}


def test_revisions_preserve_source_history_without_adoption(material):
    before = copy.deepcopy(material)
    result = clarification.audit_clarification(**material)
    assert result["active_documents"] == 4 and result["changed_document_versions"] == 2
    assert result["unchanged_source_documents"] == 2 and result["new_manuscripts"] == 0
    assert result["previous_exposure_document_versions"] == 5 and result["physical_exposure_records_as_document_versions"] == 7
    assert result["independent_policy_audit"]["passed"] == result["original_policy_cases_checked"] == 4
    assert result["author_reported_remaining_conditional_holds"] == 1
    assert result["benchmark_candidates_held"] == result["exposure_audit"]["forbidden_evaluation_count"] == 4
    assert result["previous_group_splits"] == result["benchmark_training_adopted"] == result["benchmark_evaluation_adopted"] == 0
    assert result["active_groups"] <= result["previous_groups"]
    assert all(result[k] is False for k in FLAGS)
    for name in ("semantic_equivalence_certified", "internal_reference_adoption_changed", "authoritative_parent_replaced",
                 "author_notes_are_independent_acceptance", "model_forward_executed", "training_performed"):
        assert result[name] is False
    old_rows = {r["doc_id"]: r for r in material["ledger"]["records"]}
    assert all(r == old_rows[r["doc_id"]] for r in result["exposure_ledger"]["records"] if r["doc_id"] in old_rows)
    assert len(result["exposure_audit"]["ledger_records_absent_from_current_inputs"]) == 3
    assert any(r["reason"].startswith("Preserved source exposure group") for r in result["semantic_links_remapped"])
    assert material == before


@pytest.mark.parametrize("key", ["parent_manifest_sha256", "adoption_manifest_sha256", "expected_adoption_ledger_sha256", "expected_ledger_sha256"])
def test_all_fixed_pins_fail_closed(material, key):
    material[key] = "f"*64
    with pytest.raises(FactContractError, match="fixed_pin"):
        clarification.audit_clarification(**material)


@pytest.mark.parametrize("part", ["adoption_ledger", "ledger"])
def test_rehashing_history_does_not_replace_known_pin(material, part):
    material[part]["new_untrusted_field"] = "fabricated"
    material[part]["ledger_sha256"] = value_digest({k: v for k, v in material[part].items() if k != "ledger_sha256"})
    with pytest.raises(FactContractError, match="pin|schema|hash"):
        clarification.audit_clarification(**material)


@pytest.mark.parametrize("part", ["lineage", "review_notes"])
@pytest.mark.parametrize("mutation", ["missing", "duplicate", "extra", "foreign"])
def test_exact_review_and_hold_coverage(material, part, mutation):
    items = material["revisions"][part]
    if mutation == "missing":
        items.pop()
    elif mutation == "extra":
        items.append(copy.deepcopy(items[0]))
    elif mutation == "duplicate":
        items[1] = copy.deepcopy(items[0])
    else:
        items[0]["parent_doc_id"] = material["original_records"][3]["input"]["doc_id"]
    with pytest.raises(FactContractError, match="coverage"):
        clarification.audit_clarification(**material)


@pytest.mark.parametrize("part", ["lineage", "review_notes"])
@pytest.mark.parametrize("value", [True, 0, None])
def test_review_permission_escalation_rejected(material, part, value):
    material["revisions"][part][0]["training_allowed"] = value
    with pytest.raises(FactContractError, match="permission"):
        clarification.audit_clarification(**material)


@pytest.mark.parametrize("key", ["parent_input_sha256", "child_input_sha256", "parent_body_sha256", "child_body_sha256",
    "parent_answer_sha256", "child_answer_sha256", "parent_evidence_sha256", "child_evidence_sha256", "policy_sha256", "context_sha256"])
def test_every_lineage_hash_is_bound(material, key):
    material["revisions"]["lineage"][0][key] = "f"*64
    with pytest.raises(FactContractError, match="lineage_binding"):
        clarification.audit_clarification(**material)


@pytest.mark.parametrize("key", ["family_id", "scenario_id", "template_family_id"])
def test_families_cannot_be_reset_for_false_independence(material, key):
    material["revisions"]["records"][0][key] = "invented-new-family"
    with pytest.raises(FactContractError, match="family_changed"):
        clarification.audit_clarification(**material)


@pytest.mark.parametrize("part", ["records", "answers", "evidence"])
def test_untargeted_accepted_originals_exactly_preserved(material, part):
    item = material["revisions"][part][2]
    if part == "records":
        item["domain"] = "무관한 원본 메타데이터의 부당 변경"
    elif part == "answers":
        item["other_grade_exclusions"]["S3"] = "같은 정답이라도 승인된 원본의 제외 설명을 임의 수정하지 않습니다."
    else:
        item["rationale"] = "원본의 설명을 몰래 바꾼 무관한 변경이며 검증에서 거절되어야 합니다."
    with pytest.raises(FactContractError, match="accepted_source_changed"):
        clarification.audit_clarification(**material)


def test_child_cannot_reuse_any_parent_id(material):
    material["revisions"]["lineage"][0]["child_doc_id"] = material["original_records"][0]["input"]["doc_id"]
    with pytest.raises(FactContractError, match="parent_child_coverage"):
        clarification.audit_clarification(**material)


@pytest.mark.parametrize("key,value", [("start", True), ("end", -1), ("before", "forged"), ("after_sha256", "f"*64),
                                        ("target_start", 0), ("target_end", 1)])
def test_literal_diff_forgery_fails(material, key, value):
    material["revisions"]["lineage"][0]["edits"][0][key] = value
    with pytest.raises(FactContractError, match="edit_"):
        clarification.audit_clarification(**material)


def test_diff_supports_deletion_and_replacement_but_not_unlisted_change():
    parent, child = "단위 A; 기간 B", "단위; 시점 C"
    edits = [{"start": 2, "end": 4, "before": " A", "after": "", "target_start": 2, "target_end": 2,
              "before_sha256": text_digest(" A"), "after_sha256": text_digest("")},
             {"start": 6, "end": 10, "before": "기간 B", "after": "시점 C", "target_start": 4, "target_end": 8,
              "before_sha256": text_digest("기간 B"), "after_sha256": text_digest("시점 C")}]
    clarification.replay_edits(parent, child, edits)
    with pytest.raises(FactContractError, match="unlisted|target"):
        clarification.replay_edits(parent, child+"추가", edits)


@pytest.mark.parametrize("part", ["additional_assumptions", "excluded_scope"])
@pytest.mark.parametrize("mutation", ["origin", "offset", "quote", "hash", "duplicate"])
def test_authored_assumptions_cannot_claim_observed_parent_facts(material, part, mutation):
    items = material["revisions"]["lineage"][0][part]
    if mutation == "duplicate":
        items.append(copy.deepcopy(items[0]))
    else:
        key, value = {"origin": ("origin", "observed_in_original_document"), "offset": ("start", True),
                      "quote": ("quote", "본문에 없는 진술"), "hash": ("sha256", "f"*64)}[mutation]
        items[0][key] = value
    with pytest.raises(FactContractError, match="assumption_origin|scope"):
        clarification.audit_clarification(**material)


def test_scope_shortening_can_declare_no_additional_fact(material):
    for row in material["revisions"]["lineage"]:
        row["additional_assumptions"] = []
        row["excluded_scope"] = []
    result = clarification.audit_clarification(**material)
    assert result["semantic_equivalence_certified"] is False


@pytest.mark.parametrize("mutation", ["omit", "fabricate", "rationale"])
def test_claim_and_rationale_changes_are_exact(material, mutation):
    row = material["revisions"]["lineage"][0]
    if mutation == "omit":
        row["claim_changes"] = []
    elif mutation == "fabricate":
        row["claim_changes"][0]["before"] = row["claim_changes"][0]["after"]
    else:
        row["rationale_change"]["before"] = "원본에 없던 설명을 정본으로 소급한 잘못된 기록입니다."
    with pytest.raises(FactContractError, match="claim_changes|rationale_change"):
        clarification.audit_clarification(**material)


@pytest.mark.parametrize("mutation", ["remaining", "issues", "authority", "new_manuscripts"])
def test_author_notes_cannot_promote_acceptance_or_hide_remaining_issues(material, mutation):
    note = material["revisions"]["review_notes"][0]
    if mutation == "remaining":
        note["remaining_hold"] = True
    elif mutation == "issues":
        note["remaining_issues"] = ["아직 해결하지 못한 새 조건부 검토 문제입니다."]
    elif mutation == "authority":
        note["review_kind"] = "independent_human_approval"
    else:
        material["revisions"]["lineage"][0]["new_document_count"] = 1
    with pytest.raises(FactContractError, match="authority|remaining|role"):
        clarification.audit_clarification(**material)


def test_wrong_new_grade_fails_independent_policy(material):
    material["revisions"]["answers"][0]["reference_grade"] = "S1"
    with pytest.raises(FactContractError):
        clarification.audit_clarification(**material)


def test_new_claim_quote_binding_checked(material):
    material["revisions"]["records"][0]["claims"][-1]["start"] -= 1
    with pytest.raises(FactContractError, match="claim_binding"):
        clarification.audit_clarification(**material)


@pytest.mark.parametrize("key", ["expected_previous_versions", "expected_previous_groups"])
def test_missing_prior_exposure_or_groups_fail_closed(material, key):
    material[key] += 1
    with pytest.raises(FactContractError, match="exposure|group"):
        clarification.audit_clarification(**material)
