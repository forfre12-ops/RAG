"""Build a NEW, unapproved, development-only document-form reference revision.

Offline author-fixture checks only: no NLP extraction, inference, training, real
authentication, deployment, original relabeling, or sealed semantic inspection.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from content_reference_contract import PREDICATES, require, resolve, text_hash
from content_reference_materials import (
    ALGORITHM_TEXT, BULK, CONTROL, CORE, run_toy, sensitive_table, unique_people,
)
from content_reference_review_notes import DEV_NOTES
from evaluation_inputs import normalized_hash, read_rows, sha256, text_of
from prepare_content_reference import write_json, write_jsonl
from prepare_content_reference_review import (
    PACK, POC, evidence, legacy_reviews, record_sha, source_inputs,
)

POLICY_ID = "content-protection-reference-v1.1-draft"
POLICY = POC / "docs/CONTENT_PROTECTION_REFERENCE_V1_1_DRAFT.md"
CONTRACT = POC / "docs/CONTENT_REFERENCE_INPUT_CONTRACT_V1_1.md"
REVIEW = POC / "reports/CONTENT_REFERENCE_REVIEW_20260915/round01"
REVIEW_SHA = "614074a726f49caa651baf6fefd977186d18b3cae600b59c05c002ee4bb91bda"
AS_OF = "2026-09-15T00:00:00+00:00"
THRESHOLD = 1000
VIEWS = ("body_only", "body_plus_synthetic_context")
AUTHORITY = {"approval_status": "unapproved", "review_status": "pending",
             "answer_author": "AI", "human_signature": None, "gold_eligible": False,
             "training_allowed": False, "independent_review": False}
INPUT_KEYS = {"doc_id", "family_id", "text", "text_sha256", "context", "context_sha256",
              "policy_version", "policy_sha256", "source"}
QUESTIONS = {
    "biometric_count": ("전체 원장/연결키의 동일 판본, 고유 개인 수와 익명화·재식별 상태",
                        "원문 소유자 및 개인정보 담당", "전체 합산·중복 제거·연결 상태를 같은 해시의 원장으로 확인"),
    "filled_form": ("누락/깨진 값 영역의 원본 페이지 또는 무손실 추출본",
                    "원문 소유자 및 추출 담당", "같은 판본이 빈 서식인지 개인 평가 작성본인지 직접 확인"),
    "missing_recipe": ("본문이 언급한 부록, 판본/배포 기록, 핵심 기능 재현 범위",
                       "제품 소유자 및 문서관리 담당", "부록을 확보하고 핵심성·현행성·교육본 여부의 상충 기록을 해소"),
    "price_status": ("두 숫자의 범례, 동일 판본의 거래/작업 연결과 배포 상태",
                     "거래 담당 및 문서 소유자", "실제 협상값인지 작업 순번인지 판본 일치 근거로 확인"),
    "technical_appendix": ("누락 부속서, 본문-부속서 버전 대응, 보호정보 제거/재현 범위 확인",
                           "제품 소유자 및 배포 담당", "전체 묶음을 확보하고 동일 부속서의 적용 맥락 충돌을 해소"),
}


def protected_sources() -> tuple:
    docs, answers, historical, protected = source_inputs()
    require(sha256(REVIEW / "manifest.json") == REVIEW_SHA, "Review manifest changed")
    manifest = json.loads((REVIEW / "manifest.json").read_text(encoding="utf-8"))
    protected[REVIEW / "manifest.json"] = REVIEW_SHA
    for item in manifest["files"]:
        path = (REVIEW / item["path"]).resolve()
        require(path.is_relative_to(REVIEW.resolve()), "Review path escape")
        require(sha256(path) == item["sha256"], "Frozen review output changed")
        protected[path] = item["sha256"]
    for item in manifest["source_files"]:
        path = (POC / item["path"]).resolve()
        require(path.is_relative_to(POC.resolve()), "Review source path escape")
        require(sha256(path) == item["sha256"], "Frozen review source changed")
        protected[path] = item["sha256"]
    return docs, answers, historical, protected


def model_input(doc: dict, view: str) -> str:
    """Only approved fields for each offline input view, never answer sidecars."""
    require(view in VIEWS, "Unknown input view")
    if view == "body_only":
        return doc["text"]
    return doc["text"] + "\n\n[명시적 가상 조건]\n" + json.dumps(
        doc["context"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def context_fact(value, doc_id: str) -> dict:
    return {"state": "known", "value": value, "origin": "synthetic_assumption",
            "source_ref": f"fictional-author://{doc_id}/revision-1.1"}


def build_material(key: str, grade: str) -> tuple[str, dict, dict, list[str]]:
    """Author material selected by an existing development parent, not classification."""
    full = grade == "TS"
    if key in CORE:
        title, spec, vectors, partial = CORE[key]
        if full:
            steps = ALGORITHM_TEXT[spec["op"]]
            params = json.dumps(spec, ensure_ascii=False, sort_keys=True)
            tests = "입력|기대 출력\n" + "\n".join(f"{json.dumps(v)}|{expected}" for v, expected in vectors)
            material = f"절차: {steps}\n결정 변수: {params}\n{tests}\n검증 기준: 각 시험의 정수 출력이 기대 출력과 정확히 같아야 한다."
            kind, quotes = "core", [f"절차: {steps}", f"결정 변수: {params}", tests]
        else:
            material, kind, quotes = partial, "partial", [partial]
        context_values = {"reproduction_scope": "full_toy_function" if full else "test_results_only",
                          "asset_role": "fictional_core_function", "person_linkage": False}
        fixture = {"key": key, "kind": kind, "toy_spec": spec if full else None,
                   "test_vectors": [[v, expected] for v, expected in vectors] if full else [], "table": None}
    elif key in BULK:
        total, small, _, _, title = BULK[key]
        table = sensitive_table(key, total if full else small)
        material, kind, quotes = table, "personal", [table]
        context_values = {"reproduction_scope": "not_applicable", "asset_role": "fictional_person_records",
                          "person_linkage": True}
        fixture = {"key": key, "kind": kind, "toy_spec": None, "test_vectors": [], "table": table}
    else:
        title, permission, asset, literal, partial = CONTROL[key]
        if full:
            material = (f"대상: {asset}\n요청 동작: {permission}\n제시값: {literal}\n"
                        "절차: 로컬 허구 검증기에 대상·동작·제시값을 전달하고 허용 결과를 확인한다.\n"
                        "기대 결과: 일치하면 허용, 불일치하면 거절. 현실 시스템에서는 사용하지 않는다.")
            kind, quotes = "control", [f"대상: {asset}", f"요청 동작: {permission}", f"제시값: {literal}"]
        else:
            material, kind, quotes = partial, "partial", [partial]
        context_values = {"reproduction_scope": "not_applicable", "asset_role": "fictional_core_control",
                          "person_linkage": False}
        fixture = {"key": key, "kind": kind, "toy_spec": None, "test_vectors": [], "table": None}
        if full:
            context_values["control_record"] = {
                "asset": asset, "permission": permission, "material_sha256": text_hash(literal),
                "valid_from": "2026-09-01T00:00:00+00:00", "valid_until": "2026-10-01T00:00:00+00:00",
                "revoked": False, "additional_factor_required": False, "all_required_material_supplied": True,
                "world": "fictional_only", "real_system_validity": False}
    text = (f"# {title} — 합성 문서형 시험자료\n"
            "모든 인물·값·단위·기능은 허구이며 현실의 제조·인증·거래·개인정보가 아니다.\n"
            "허구 세계 안의 보호 필요를 묻는 시험이며, 현실 자료의 관리등급을 선언하지 않는다.\n\n" + material)
    return text, fixture, context_values, quotes


def control_condition(doc: dict) -> bool | None:
    """Metadata arithmetic only; does not prove real credential validity."""
    facts = doc["context"]["facts"]
    node = facts.get("control_record")
    if node is None or node["state"] == "unknown":
        return None
    value = node["value"]
    required = {"asset", "permission", "material_sha256", "valid_from", "valid_until", "revoked",
                "additional_factor_required", "all_required_material_supplied", "world", "real_system_validity"}
    if not isinstance(value, dict) or set(value) != required:
        return None
    require(value["world"] == "fictional_only" and value["real_system_validity"] is False,
            "Never treat fixture controls as real credentials")
    for name in ("revoked", "additional_factor_required", "all_required_material_supplied"):
        require(type(value[name]) is bool, "Control state must be boolean")
    try:
        start, end, now = (datetime.fromisoformat(v) for v in
                           (value["valid_from"], value["valid_until"], doc["context"]["as_of"]))
        require(all(v.tzinfo is not None for v in (start, end, now)) and start < end, "Invalid control time scope")
    except (ValueError, TypeError):
        return None
    lines = doc["text"].splitlines()
    literals = [line.removeprefix("제시값: ") for line in lines if line.startswith("제시값: ")]
    present = len(literals) == 1 and text_hash(literals[0]) == value["material_sha256"]
    target = f"대상: {value['asset']}" in lines and f"요청 동작: {value['permission']}" in lines
    return (present and target and start <= now < end and not value["revoked"]
            and not value["additional_factor_required"] and value["all_required_material_supplied"])


def fixture_facts(doc: dict, fixture: dict, view: str) -> dict:
    """Check declared fixture observations, NOT extraction from arbitrary prose."""
    require(view in VIEWS, "Unknown input view")
    if view == "body_only":
        return dict.fromkeys(PREDICATES)  # The fictional-world context was withheld.
    ctx = doc["context"]["facts"]

    def known(name):
        node = ctx.get(name)
        return node["value"] if node is not None and node["state"] == "known" else None

    result = dict.fromkeys(PREDICATES, False)
    kind = fixture["kind"]
    nonpublic = known("distribution") == "not_released_in_fiction" if known("distribution") is not None else None
    if kind == "core":
        scope, role = known("reproduction_scope"), known("asset_role")
        result["core_package"] = (None if None in (scope, role, nonpublic) else
                                  scope == "full_toy_function" and role == "fictional_core_function" and nonpublic)
        result["specific"] = nonpublic
    elif kind == "personal":
        linked = known("person_linkage")
        count = unique_people(fixture["table"])
        result["bulk_sensitive"] = None if linked is None else linked and count >= THRESHOLD
        result["specific"] = None if linked is None else linked and count > 0
    elif kind == "control":
        result["live_control"] = control_condition(doc)
        result["internal"] = nonpublic
    else:
        require(kind == "partial", "Unknown fixture type")
        result["specific"] = nonpublic
    if known("scope_complete") is not True:
        # No upper-risk absence claim from missing scope. Proven TS survives.
        result = {key: True if value is True else None for key, value in result.items()}
    return result


def fixture_decision(doc: dict, fixture: dict, view: str, *, unresolved_reason: str | None = None) -> dict:
    """Offline review-reason contract. No production routing changes."""
    require(unresolved_reason in {None, "context_conflict", "policy_gap"}, "Unknown unresolved reason")
    if unresolved_reason:
        return {"grade": None, "status": "needs_policy_review", "rule_ids": ["CP-HOLD-02"],
                "reason_code": unresolved_reason, "candidate_grades": [], "candidate_bounds_are_absolute": False}
    result = resolve(fixture_facts(doc, fixture, view))
    return result | {"reason_code": "missing_evidence" if result["grade"] is None else "fictional_conditions_only",
                     "candidate_bounds_are_absolute": False}


def make_cases(docs: dict, parents: dict) -> tuple[list, list, list]:
    rows, answers, lineage = [], [], []
    for parent_id, grade, disposition, *_ in DEV_NOTES:
        if disposition != "amend_material":
            continue
        parent, prior = docs[parent_id], parents[parent_id]
        require(grade == prior["expected_grade"] and grade in {"TS", "S1"}, "Parent grade drift")
        key = prior["authoring_source"]["catalog_key"]
        doc_id = "CM-" + text_hash(parent_id + POLICY_ID)[:16]
        body, fixture, values, quotes = build_material(key, grade)
        values |= {"scope_complete": True, "distribution": "not_released_in_fiction"}
        context = {"world": "fictional_only", "document_version": "material-1.1", "as_of": AS_OF,
                   "subject_text_sha256": text_hash(body), "scope": "body_only_no_attachments_in_fiction",
                   "facts": {name: context_fact(value, doc_id) for name, value in values.items()}}
        row = {"doc_id": doc_id, "family_id": parent["family_id"], "text": body, "text_sha256": text_hash(body),
               "context": context, "context_sha256": record_sha(context), "policy_version": POLICY_ID,
               "policy_sha256": sha256(POLICY),
               "source": {"kind": "synthetic_document_form", "author": "AI", "real_document": False}}
        spans = [evidence(body, q, "body_direct_value") | {"id": f"B{i}"} for i, q in enumerate(quotes, 1)]
        context_evidence = [{"id": f"C-{name}", "origin": "synthetic_assumption", "pointer": f"/facts/{name}",
                             "value_sha256": record_sha(node), "context_sha256": row["context_sha256"]}
                            for name, node in context["facts"].items()]
        rule = prior["rule_ids"][0]
        views = {
            "body_only": {"expected_grade": None, "expected_status": "needs_evidence", "reason_code": "context_withheld",
                          "rule_ids": ["CP-HOLD-01"], "grade_metric_eligible": False},
            "body_plus_synthetic_context": {"expected_grade": grade, "expected_status": "recommended",
                                            "reason_code": "fictional_conditions_only", "rule_ids": [rule],
                                            "grade_metric_eligible": True},
        }
        for view in VIEWS:
            views[view]["input_sha256"] = text_hash(model_input(row, view))
        answer = AUTHORITY | {
            "doc_id": doc_id, "family_id": row["family_id"], "parent_doc_id": parent_id,
            "parent_answer_sha256": record_sha(prior), "input_record_sha256": record_sha(row),
            "text_sha256": row["text_sha256"], "context_sha256": row["context_sha256"],
            "policy_version": POLICY_ID, "policy_sha256": row["policy_sha256"], "views": views,
            "fixture": fixture, "body_evidence": spans, "context_evidence": context_evidence,
            "rule_evidence_ids": {rule: [e["id"] for e in spans + context_evidence]},
            "not_higher_reason": ("정책 내 최상위. 현실 피해의 상한이라는 뜻은 아님." if grade == "TS" else
                                  "완결된 이 부분 보고서/소수 원장 범위에는 완전 핵심 재현·유효 통제·임계 인원 조건이 없다. 범위는 가상 맥락으로만 확인."),
            "not_lower_reason": ("직접 수록한 재현 명세/원장/데모 재료와 입력에 제공한 가상 맥락이 해당 TS 조건을 충족." if grade == "TS" else
                                 "직접 수록된 개인 세부행·제품별 시험/코드 일부·거래 조건은 일반 운영값보다 구체적인 보호 내용."),
            "authority_note": "후보 답과 검산 코드가 같은 AI 작성자. 독립 정답 인증·고객사 정확도 아님.",
        }
        rows.append(row)
        answers.append(answer)
        lineage.append({"doc_id": doc_id, "parent_doc_id": parent_id, "family_id": parent["family_id"],
                        "parent_text_sha256": parent["text_sha256"], "new_text_sha256": row["text_sha256"],
                        "parent_answer_sha256": record_sha(prior), "split": "development_only",
                        "change_type": "new_body_and_context_not_label_only_ab", "training_allowed": False})
    return rows, answers, lineage


def impact_rows(docs: dict, parents: dict) -> list[dict]:
    result = []
    for doc_id, expected, disposition, quote, finding, _ in DEV_NOTES:
        if disposition == "amend_material":
            continue
        prior = parents[doc_id]
        key = prior["authoring_source"]["catalog_key"]
        query, owner, closure = QUESTIONS[key] if disposition == "needs_context" else (
            "실제 판정 시점(as_of), 같은 판본의 전체 범위와 배포 상태", "문서 소유자",
            "실제 문서 평가로 전환할 때 절대 시점·누락 첨부·안전한 공개 여부 확인")
        result.append(AUTHORITY | {
            "doc_id": doc_id, "family_id": docs[doc_id]["family_id"], "text_sha256": docs[doc_id]["text_sha256"],
            "parent_answer_sha256": record_sha(prior), "policy_version": POLICY_ID, "policy_sha256": sha256(POLICY),
            "disposition": "retain_as_scenario_only" if disposition == "keep" else "keep_review_pending",
            "candidate_grade": prior["expected_grade"], "candidate_status": prior["expected_status"],
            "candidate_scope": "original_fictional_conditions_only_not_a_revised_model_gold",
            "reason_code": ("concept_case_only" if disposition == "keep" else
                            "context_conflict" if expected == "needs_policy_review" else "missing_evidence"),
            "finding": finding, "evidence": evidence(docs[doc_id]["text"], quote, "content_assertion"),
            "request": query, "resolution_owner_role": owner, "closure_condition": closure,
            "unchanged_text_model_answer_adjudicated": False, "original_label_replacement": None,
            "candidate_bounds_are_absolute_risk_ceiling": False,
        })
    return result


def legacy_followups(historical: list) -> list[dict]:
    rows = legacy_reviews(historical)
    for row in rows:
        row["prior_reference_policy"] = row["reference_policy"]
        row["reference_policy"] = {"version": POLICY_ID, "sha256": sha256(POLICY)}
        row["proposal"]["scope"] = "v1.1_conditional_interpretation_not_legacy_truth"
        row["missing_context_not_fabricated"] = True
        row["draft_change_effect"] = {
            "a1beb524ceafe108": "수요/우선순위는 S1 범위로 명문화했으나 기준일·동일 판본 배포 상태는 미확인. 보류 유지.",
            "823545b7edf3a0ef": "비용·수급 판단을 S1에 명시. 원문 가상 조건 내 S1 제안 유지, 실제 시점/범위 확인과 책임자 검토 필요.",
            "9a4ace0da18602c1": "credential 등의 명칭은 직접값이 아님. 실제 원문·유효성·권한 증거 전에는 TS/S1을 확정하지 않음.",
            "6ea073680b55d1e9": "견적/협상 유형명은 실질 거래조건이 아님. 견적 값/조건과 원문 범위 확인 전 보류.",
        }[row["doc_id"]]
    return rows


def validate(rows: list, answers: list, lineage: list, docs: dict, parents: dict) -> dict:
    require(len(rows) == len(answers) == len(lineage) == 40, "Expected 40 material cases")
    inputs = {r["doc_id"]: r for r in rows}
    answer_map = {r["doc_id"]: r for r in answers}
    links = {r["doc_id"]: r for r in lineage}
    require(len(inputs) == len(answer_map) == len(links) == 40 and inputs.keys() == answer_map.keys() == links.keys(),
            "Duplicate/mismatched IDs")
    target_parents = {n[0] for n in DEV_NOTES if n[2] == "amend_material"}
    require({a["parent_doc_id"] for a in answers} == target_parents, "Parent coverage mismatch")
    require(Counter(a["views"][VIEWS[1]]["expected_grade"] for a in answers) == {"TS": 20, "S1": 20}, "Grade balance mismatch")
    require(len({normalized_hash(r["text"]) for r in rows}) == 40, "Duplicate normalized body")
    require(not ({normalized_hash(r["text"]) for r in rows} & {normalized_hash(r["text"]) for r in docs.values()}),
            "Revision copied original scenario body")
    policy_text = POLICY.read_text(encoding="utf-8")
    require(POLICY_ID in policy_text and "1,000" in policy_text, "Policy registry missing")
    body_spans, tests, bulk_n = 0, 0, 0
    for doc_id, doc in inputs.items():
        answer, link = answer_map[doc_id], links[doc_id]
        require(set(doc) == INPUT_KEYS and doc["source"] == {"kind": "synthetic_document_form", "author": "AI", "real_document": False},
                "Input contract/authority violation")
        require(doc["text_sha256"] == text_hash(doc["text"]) == answer["text_sha256"], "Body binding mismatch")
        require(doc["context_sha256"] == record_sha(doc["context"]) == answer["context_sha256"], "Context binding mismatch")
        require(answer["input_record_sha256"] == record_sha(doc), "Input record binding mismatch")
        require(doc["context"]["subject_text_sha256"] == doc["text_sha256"], "Context subject mismatch")
        require(doc["context"]["world"] == "fictional_only", "Synthetic context scope changed")
        require(doc["context"]["as_of"] == AS_OF and doc["context"]["document_version"] == "material-1.1" and
                doc["context"]["scope"] == "body_only_no_attachments_in_fiction", "Context time/version/scope drift")
        require(set(doc["context"]) == {"world", "document_version", "as_of", "subject_text_sha256", "scope", "facts"},
                "Unexpected context fields/answer leak")
        for node in doc["context"]["facts"].values():
            require(set(node) == {"state", "value", "origin", "source_ref"} and node["state"] in {"known", "unknown"}
                    and node["origin"] == "synthetic_assumption" and node["source_ref"] == f"fictional-author://{doc_id}/revision-1.1",
                    "Invalid typed source or invented authority")
            require((node["state"] == "unknown") == (node["value"] is None), "Unknown/value mismatch")
        for row in (doc, answer):
            require(row["policy_version"] == POLICY_ID and row["policy_sha256"] == sha256(POLICY), "Policy binding mismatch")
        require(all(answer[k] == v and type(answer[k]) is type(v) for k, v in AUTHORITY.items()), "Fabricated answer authority")
        parent_id = answer["parent_doc_id"]
        require(answer["parent_answer_sha256"] == record_sha(parents[parent_id]), "Parent answer binding mismatch")
        require(doc["family_id"] == answer["family_id"] == docs[parent_id]["family_id"], "Family leakage")
        require(link == {"doc_id": doc_id, "parent_doc_id": parent_id, "family_id": doc["family_id"],
                         "parent_text_sha256": docs[parent_id]["text_sha256"], "new_text_sha256": doc["text_sha256"],
                         "parent_answer_sha256": answer["parent_answer_sha256"], "split": "development_only",
                         "change_type": "new_body_and_context_not_label_only_ab", "training_allowed": False}, "Lineage mismatch")
        fixture = answer["fixture"]
        require(fixture["key"] == parents[parent_id]["authoring_source"]["catalog_key"], "Fixture family mismatch")
        intended_grade = parents[parent_id]["expected_grade"]
        expected_body, expected_fixture, expected_ctx, expected_quotes = build_material(fixture["key"], intended_grade)
        require(doc["text"] == expected_body and fixture == expected_fixture, "Authored material drift")
        expected_ctx |= {"scope_complete": True, "distribution": "not_released_in_fiction"}
        require(doc["context"]["facts"] == {k: context_fact(v, doc_id) for k, v in expected_ctx.items()}, "Authored context drift")
        require(set(answer["views"]) == set(VIEWS), "Missing input view")
        for view in VIEWS:
            proposed = answer["views"][view]
            require(proposed["input_sha256"] == text_hash(model_input(doc, view)), "View hash mismatch")
            calculated = fixture_decision(doc, fixture, view)
            require((proposed["expected_grade"], proposed["expected_status"], proposed["rule_ids"]) ==
                    (calculated["grade"], calculated["status"], calculated["rule_ids"]), "Fixture/answer contradiction")
            require(proposed["grade_metric_eligible"] is (view == VIEWS[1]), "Hidden-context scoring forbidden")
            require(all(rule in policy_text for rule in proposed["rule_ids"]), "Unbound rule")
        require(answer["views"][VIEWS[1]]["expected_grade"] == intended_grade, "Candidate no longer matches intended parent grade")
        require([e["quote"] for e in answer["body_evidence"]] == expected_quotes, "Missing direct-value evidence")
        for e in answer["body_evidence"]:
            require(type(e["start"]) is int and type(e["end"]) is int and
                    0 <= e["start"] < e["end"] <= len(doc["text"]) and
                    doc["text"][e["start"]:e["end"]] == e["quote"] and e["origin"] == "body_direct_value", "Invalid evidence span")
            body_spans += 1
        require(len(answer["context_evidence"]) == len(expected_ctx), "Missing context evidence")
        require({e["pointer"] for e in answer["context_evidence"]} == {f"/facts/{k}" for k in expected_ctx}, "Context pointer coverage")
        for e in answer["context_evidence"]:
            name = e["pointer"].removeprefix("/facts/")
            require(e["value_sha256"] == record_sha(doc["context"]["facts"][name]) and e["origin"] == "synthetic_assumption"
                    and e["context_sha256"] == doc["context_sha256"], "Invalid context evidence")
        all_evidence = answer["body_evidence"] + answer["context_evidence"]
        ids = [e["id"] for e in all_evidence]
        require(len(ids) == len(set(ids)) and answer["rule_evidence_ids"] ==
                {parents[parent_id]["rule_ids"][0]: ids}, "Rule evidence binding mismatch")
        require(bool(answer["not_higher_reason"]) and bool(answer["not_lower_reason"]), "Missing boundary rationale")
        for values, expected in fixture["test_vectors"]:
            require(run_toy(fixture["toy_spec"], values) == expected, "Toy arithmetic mismatch")
            tests += 1
        if fixture["kind"] == "personal":
            count = unique_people(fixture["table"])
            require(count == len(fixture["table"].splitlines()) - 1, "Duplicate fixture persons")
            if intended_grade == "TS":
                bulk_n += count
    return {"status": "REVISION_FIXTURE_CHECKS_OK", "material_cases": 40, "context_view_grade_counts": {"TS": 20, "S1": 20},
            "body_only_grade_scored": 0, "body_only_review_cases": 40, "direct_evidence_spans": body_spans,
            "toy_vector_checks": tests, "large_ledger_unique_people": bulk_n, "training_allowed": False,
            "model_executed": False, "customer_accuracy_measured": False, "independent_review": False}


def known_overlap(rows: list) -> list:
    original = json.loads((PACK / "validation.json").read_text(encoding="utf-8"))
    pools = original["known_pool_overlap"]["pools"]
    hashes = {normalized_hash(r["text"]) for r in rows}
    result = []
    for pool in pools:
        name = pool["path"]
        path = POC / name
        require(path.is_file(), f"Known overlap pool missing: {name}")
        require(path.resolve().is_relative_to(POC.resolve()) and sha256(path) == pool["sha256"], "Known pool drift")
        old = read_rows(path)
        result.append({"path": name, "sha256": sha256(path), "rows": len(old),
                       "normalized_body_overlap": len(hashes & {normalized_hash(text_of(r)) for r in old}),
                       "rows_without_family_id": sum(not r.get("family_id") for r in old),
                       "semantic_family_overlap_verified": False})
    return result


def render_index(rows: list, answers: list) -> str:
    lines = ["# 합성 문서형 보완40건", "", "AI 후보·미승인·학습 금지. 현실 고객사 등급/정답 인증이 아니다.", "",
             "본문만은 전체 등급 보류40건; 명시적 가상 맥락 포함 관점은 TS20/S1 20건.", "",
             "| 새 ID | 부모 ID | 내용 유형 | 가상 맥락 포함 후보 |", "|---|---|---|---|"]
    for a in answers:
        lines.append(f"| {a['doc_id']} | {a['parent_doc_id']} | {a['fixture']['kind']} | {a['views'][VIEWS[1]]['expected_grade']} |")
    lines += ["", "입력/맥락 원문: inputs.jsonl. 답안/근거: answers.candidate.jsonl.", "",
              "documents/*.md는 입력만, candidates/*.md는 AI 답안과 근거다. 독립 검토자는 입력을 먼저 판단한다.", "",
              "이 분리는 편의 기능이지 접근통제나 독립 블라인드 봉인 완료가 아니다.", ""]
    return "\n".join(lines)


def write_text(path: Path, value: str):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(value)


def build(out: Path) -> dict:
    out = out.resolve()
    require(not out.exists(), "Output exists; choose a NEW directory")
    for frozen in (PACK, REVIEW):
        require(not out.is_relative_to(frozen.resolve()), "Never write inside a frozen pack")
    docs, parents, historical, protected = protected_sources()
    rows, answers, lineage = make_cases(docs, parents)
    summary = validate(rows, answers, lineage, docs, parents)
    impacts, legacy = impact_rows(docs, parents), legacy_followups(historical)
    require(len(impacts) == 50 and Counter(r["disposition"] for r in impacts) ==
            {"retain_as_scenario_only": 40, "keep_review_pending": 10}, "Impact coverage mismatch")
    summary |= {"impact_cases": 50, "legacy_cases": len(legacy), "known_pool_overlap": known_overlap(rows),
                "sealed_semantically_read": False, "original_labels_changed": 0}
    require(all(sha256(p) == digest for p, digest in protected.items()), "Protected source changed during build")
    out.mkdir(parents=True, exist_ok=False)
    for name, content in (("inputs.jsonl", rows), ("answers.candidate.jsonl", answers), ("lineage.jsonl", lineage),
                          ("impact50.jsonl", impacts), ("legacy_followup4.jsonl", legacy)):
        write_jsonl(out / name, content)
    write_jsonl(out / "review_template.jsonl", [{"doc_id": r["doc_id"], "text_sha256": r["text_sha256"],
                "context_sha256": r["context_sha256"], "policy_version": POLICY_ID, "policy_sha256": sha256(POLICY),
                "view": VIEWS[1], "input_sha256": text_hash(model_input(r, VIEWS[1])),
                "reviewer_grade": None, "reviewer_status": None, "rule_ids": [], "evidence": [],
                "reviewer_id": None, "reviewed_at": None, "disagreement_reason": None} for r in rows])
    write_json(out / "validation.json", summary)
    write_json(out / "preservation.json", {"status": "PRESERVED", "checked_files": len(protected),
               "sealed_files_hashed_only": True, "files": [{"path": p.relative_to(POC).as_posix(), "sha256": h}
                                                           for p, h in sorted(protected.items())]})
    write_text(out / "README.md", render_index(rows, answers))
    (out / "documents").mkdir()
    (out / "candidates").mkdir()
    for row, answer in zip(rows, answers):
        write_text(out / "documents" / f"{row['doc_id']}.md", model_input(row, VIEWS[1]) + "\n")
        write_text(out / "candidates" / f"{row['doc_id']}.md",
                   "# AI 후보 답안 — 미승인\n\n```json\n" + json.dumps(answer, ensure_ascii=False, indent=2) + "\n```\n")
    write_text(out / "IMPACT50.md", "# 유지40·검토10 개정 영향\n\n" + "\n\n".join(
        f"## {r['doc_id']}\n\n{r['disposition']}: {r['candidate_grade'] or r['candidate_status']}\n\n"
        f"{r['finding']}\n\n요청: {r['request']}\n\n해결 역할: {r['resolution_owner_role']}\n\n완료 조건: {r['closure_condition']}"
        for r in impacts) + "\n")
    write_text(out / "LEGACY_FOLLOWUP4.md", "# 기존4건 새 기준 후속 기록 — 원라벨 변경 없음\n\n" + "\n\n".join(
        f"## {r['doc_id']}\n\n{r['draft_change_effect']}\n\n후보: {r['proposal']['grade'] or r['proposal']['status']}"
        f"\n\n필요 조치: {r['next_action']}" for r in legacy) + "\n")
    sources = [Path(__file__), Path(__file__).with_name("content_reference_materials.py"), POLICY, CONTRACT,
               Path(__file__).with_name("content_reference_contract.py"), Path(__file__).with_name("evaluation_inputs.py"),
               Path(__file__).with_name("prepare_content_reference.py"), Path(__file__).with_name("prepare_content_reference_review.py"),
               Path(__file__).with_name("content_reference_review_notes.py")]
    write_json(out / "manifest.json", {"schema_version": "content-reference-material-revision-v1.1",
               "created_at": datetime.now(timezone.utc).isoformat(), "policy_version": POLICY_ID, "policy_sha256": sha256(POLICY),
               "git_head": subprocess.check_output(["git", "-C", str(POC), "rev-parse", "HEAD"], text=True).strip(),
               "git_head_is_not_source_snapshot": True, "approval_status": "unapproved", "training_allowed": False,
               "split": "development_only", "original_review_manifest_sha256": REVIEW_SHA,
               "source_files": [{"path": p.relative_to(POC).as_posix(), "sha256": sha256(p)} for p in sources],
               "files": [{"path": p.relative_to(out).as_posix(), "sha256": sha256(p)} for p in sorted(out.rglob("*")) if p.is_file()]})
    return summary


def check_saved_pack(pack: Path) -> dict:
    """Read-back verification, including all generated file hashes and provenance."""
    pack = pack.resolve()
    manifest = json.loads((pack / "manifest.json").read_text(encoding="utf-8"))
    require(manifest["schema_version"] == "content-reference-material-revision-v1.1" and
            manifest["policy_version"] == POLICY_ID and manifest["policy_sha256"] == sha256(POLICY), "Saved policy mismatch")
    require(manifest["approval_status"] == "unapproved" and manifest["training_allowed"] is False and
            manifest["split"] == "development_only" and manifest["original_review_manifest_sha256"] == REVIEW_SHA,
            "Saved authority/split mismatch")
    for root, items in ((pack, manifest["files"]), (POC, manifest["source_files"])):
        require(len({item["path"] for item in items}) == len(items), "Duplicate manifest paths")
        for item in items:
            path = (root / item["path"]).resolve()
            require(path.is_relative_to(root.resolve()) and sha256(path) == item["sha256"], "Saved file/source hash mismatch")
    actual = {p.relative_to(pack).as_posix() for p in pack.rglob("*") if p.is_file() and p != pack / "manifest.json"}
    require(actual == {item["path"] for item in manifest["files"]}, "Unmanifested/missing pack file")
    docs, parents, historical, protected = protected_sources()
    rows, answers, lineage = (read_rows(pack / name) for name in ("inputs.jsonl", "answers.candidate.jsonl", "lineage.jsonl"))
    summary = validate(rows, answers, lineage, docs, parents)
    require(read_rows(pack / "impact50.jsonl") == impact_rows(docs, parents), "Impact sidecar drift")
    require(read_rows(pack / "legacy_followup4.jsonl") == legacy_followups(historical), "Legacy sidecar drift")
    for row, answer in zip(rows, answers):
        require((pack / "documents" / f"{row['doc_id']}.md").read_text(encoding="utf-8") == model_input(row, VIEWS[1]) + "\n",
                "Rendered input mismatch")
        expected_answer = "# AI 후보 답안 — 미승인\n\n```json\n" + json.dumps(answer, ensure_ascii=False, indent=2) + "\n```\n"
        require((pack / "candidates" / f"{row['doc_id']}.md").read_text(encoding="utf-8") == expected_answer, "Rendered answer mismatch")
    blank = read_rows(pack / "review_template.jsonl")
    require(len(blank) == 40 and {r["doc_id"] for r in blank} == {r["doc_id"] for r in rows}, "Reviewer template coverage")
    require(all(r["reviewer_grade"] is None and r["reviewer_status"] is None and r["reviewer_id"] is None and
                r["reviewed_at"] is None and r["rule_ids"] == [] and r["evidence"] == [] for r in blank), "Reviewer template is not blank")
    require(all(sha256(path) == digest for path, digest in protected.items()), "Original preservation failure")
    summary |= {"impact_cases": 50, "legacy_cases": 4, "known_pool_overlap": known_overlap(rows),
                "sealed_semantically_read": False, "original_labels_changed": 0}
    require(json.loads((pack / "validation.json").read_text(encoding="utf-8")) == summary, "Saved summary drift")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--out")
    mode.add_argument("--check-pack")
    args = parser.parse_args()
    result = check_saved_pack(POC / args.check_pack) if args.check_pack else build(POC / args.out)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
