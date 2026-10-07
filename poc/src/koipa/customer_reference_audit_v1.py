"""Separate implementation check of synthetic policy 0.1, not a truth authority.

The expected decision never imports the author's reference engine. Facts are
decoded from the model-visible context, not answer/evidence sidecars. Shared
schema/hash helpers verify bindings only; they do not certify prose semantics.
"""
from __future__ import annotations

from collections import Counter

from koipa.customer_benchmark import FLAGS, GRADES, validate_answers, validate_documents
from koipa.policy_facts import require, text_digest, value_digest

POLICY_ID = "customer-guide-synthetic-reference"
POLICY_VERSION = "0.1"
POLICY_SHA256 = "e3aa2854120397342574c2487a46c83a4b74b9108c706bbc3c4b47c8dfbe1ac9"

# Literal oracle: no production formula lookup, multiplication or count-of-twos
# is used to derive the expected grade. Product remains an explanatory check.
TRUTH_TABLE = {
    (0, 0, 0): "S3", (0, 0, 1): "S3", (0, 0, 2): "S3",
    (0, 1, 0): "S3", (0, 1, 1): "S3", (0, 1, 2): "S3",
    (0, 2, 0): "S3", (0, 2, 1): "S3", (0, 2, 2): "S3",
    (1, 0, 0): "S3", (1, 0, 1): "S3", (1, 0, 2): "S3",
    (1, 1, 0): "S3", (1, 1, 1): "S2", (1, 1, 2): "S2",
    (1, 2, 0): "S3", (1, 2, 1): "S2", (1, 2, 2): "S1",
    (2, 0, 0): "S3", (2, 0, 1): "S3", (2, 0, 2): "S3",
    (2, 1, 0): "S3", (2, 1, 1): "S2", (2, 1, 2): "S1",
    (2, 2, 0): "S3", (2, 2, 1): "S1", (2, 2, 2): "TS",
}

GROUP_FIELDS = {
    "reader_scope": (
        ("public_exact_body", "해당 판본 전체의 외부 공개", bool),
        ("obtainable_without_holder", "보유자를 거치지 않은 취득 가능", bool),
        ("ordinary_access_difficult", "통상 취득의 어려움", bool)),
    "impact_description": (
        ("cost_krw", "해당 정보 취득개발 비용(원)", int),
        ("person_hours", "해당 정보 취득개발 투입(인시)", int),
        ("economic_utility", "경제적 활용 가능", bool),
        ("investment_scope_exact", "투입 기록의 해당 정보 귀속 확인", bool)),
    "management_controls": (
        ("secrecy_manageable", "비밀 유지 가능", bool),
        ("all_staff_knows", "전 직원이 알아야 함", bool),
        ("business_need_only", "업무 필요자만 열람", bool),
        ("individual_approval", "개별 승인자만 열람", bool),
        ("access_enforced", "접근 제한 실제 적용", bool)),
}
OPTIONAL = ("release_authorized", "other_risk_present")
REQUIRED = tuple(key for fields in GROUP_FIELDS.values() for key, _, _ in fields)
TYPES = {key: typ for fields in GROUP_FIELDS.values() for key, _, typ in fields} | dict.fromkeys(OPTIONAL, bool)
S_TABLE = {(True, True, False): 0, (False, True, True): 1, (False, False, True): 2}
# Ordered as manageable/all-staff/business-need/individual-approval/enforced.
M_TABLE = {
    (False, False, False, False, False): 0,
    (False, True, False, False, False): 0,
    (True, True, False, False, False): 0,
    (True, False, True, False, True): 1,
    (True, False, True, True, True): 2,
}


def parse_visible_context(context):
    """Parse the canonical five fields without the generator's decoder."""
    require(type(context) is list and len(context) == 5, "independent_context_count")
    facts = {}
    for i, name in enumerate((*GROUP_FIELDS, *OPTIONAL)):
        row = context[i]
        require(type(row) is dict and set(row) == {"name", "value", "origin"}, "independent_context_shape")
        require(row["name"] == name and row["origin"] == "synthetic_assumption", "independent_context_identity")
        if name in OPTIONAL:
            value = row["value"]
            require(value is None or type(value) is bool, "independent_context_boolean")
            facts[name] = value
            continue
        require(type(row["value"]) is str, "independent_context_text")
        parts = row["value"].split("; ")
        require(len(parts) == len(GROUP_FIELDS[name]), "independent_context_entries")
        for part, (key, label, typ) in zip(parts, GROUP_FIELDS[name], strict=True):
            prefix = label + ": "
            require(part.startswith(prefix), "independent_context_label")
            raw = part[len(prefix):]
            if raw == "미수신":
                value = None
            elif typ is bool:
                require(raw in ("예", "아니오"), "independent_context_boolean")
                value = raw == "예"
            else:
                require(raw.isascii() and raw.isdecimal() and raw == str(int(raw)), "independent_context_integer")
                value = int(raw)
                require(value <= 10**12, "independent_context_range")
            facts[key] = value
    return facts


def expected_decision(facts):
    """Apply separately transcribed policy tables to explicit fictional facts."""
    require(type(facts) is dict and set(facts) == set(TYPES), "independent_fact_fields")
    for key, typ in TYPES.items():
        value = facts[key]
        require(value is None or type(value) is typ, "independent_fact_type")
        if typ is int and value is not None:
            require(0 <= value <= 10**12, "independent_fact_range")
    missing = [key for key in REQUIRED if facts[key] is None]
    levels = {"S": None, "V": None, "M": None}
    reasons = []
    if missing:
        reasons.append("required_evidence_unknown")
    else:
        s_key = tuple(facts[k] for k, _, _ in GROUP_FIELDS["reader_scope"])
        levels["S"] = S_TABLE.get(s_key)
        if levels["S"] is None:
            reasons.append("conflicting_public_access" if facts["public_exact_body"] else "secrecy_outside_clear_anchors")

        cost, hours = facts["cost_krw"], facts["person_hours"]
        if not facts["investment_scope_exact"]:
            reasons.append("investment_not_attributable")
        elif not facts["economic_utility"]:
            if cost == 0 and hours == 0:
                levels["V"] = 0
            else:
                reasons.append("conflicting_value_premises")
        elif cost >= 30_000_000 or hours >= 480:
            levels["V"] = 2
        elif cost <= 1_000_000 and hours <= 40 and (cost > 0 or hours > 0):
            levels["V"] = 1
        else:
            reasons.append("value_outside_clear_anchors")

        m_key = tuple(facts[k] for k, _, _ in GROUP_FIELDS["management_controls"])
        levels["M"] = M_TABLE.get(m_key)
        if levels["M"] is None:
            reasons.append("conflicting_management_premises" if facts["all_staff_knows"] or not facts["secrecy_manageable"] else "management_not_demonstrated")
    complete = not reasons and all(level is not None for level in levels.values())
    grade = TRUTH_TABLE[tuple(levels.values())] if complete else None
    product = levels["S"] * levels["V"] * levels["M"] if complete else None
    rules = [f"guide-s-{levels['S']}", f"trial-v-{levels['V']}", f"guide-m-{levels['M']}", "guide-product-four-level"] if complete else []
    return {"status": "conditional_reference" if complete else "HOLD", "reference_grade": grade,
            "factors": levels, "product": product, "rule_ids": rules,
            "missing_evidence": missing, "reasons": reasons,
            "release_authorized_in_scenario": facts["release_authorized"],
            "separate_disclosure_review_required": facts["release_authorized"] is not True or facts["other_risk_present"] is not False,
            "disclosure_permission_granted_by_classifier": False}


def _context_evidence(context):
    return [{"name": key, "origin": "synthetic_assumption", "pointer": f"/context/{i}/value",
             "quote": context[i]["value"], "sha256": value_digest(context[i]["value"])}
            for i, name in enumerate((*GROUP_FIELDS, *OPTIONAL))
            for key in ([k for k, _, _ in GROUP_FIELDS[name]] if name in GROUP_FIELDS else [name])]


def audit_reference(records, answers, details):
    """Fail closed on any disagreement; accepts any nonempty size, including 196."""
    before = value_digest([records, answers, details])
    docs = validate_documents(records)
    checked_answers = validate_answers(docs, answers)
    require(type(details) is list and len(details) == len(docs), "independent_detail_count")
    require(all(type(row) is dict and type(row.get("doc_id")) is str for row in details), "independent_detail_shape")
    detail_by_id = {row["doc_id"]: row for row in details}
    answer_by_id = {row.doc_id: row for row in checked_answers}
    require(len(detail_by_id) == len(docs) and set(detail_by_id) == set(answer_by_id), "independent_detail_ids")
    rows = []
    for doc in docs:
        doc_id = doc.input.doc_id
        detail, answer = detail_by_id[doc_id], answer_by_id[doc_id]
        context = [row.model_dump() for row in doc.input.context]
        facts = parse_visible_context(context)
        expected = expected_decision(facts)
        require(expected["status"] == "conditional_reference", "independent_fixed_answer_for_hold")
        require((answer.policy_id, answer.policy_version, answer.policy_sha256) ==
                (POLICY_ID, POLICY_VERSION, POLICY_SHA256), "independent_policy_mismatch")
        require(answer.reference_grade == expected["reference_grade"] and answer.rule_ids == expected["rule_ids"], "independent_answer_mismatch")
        require(value_digest(detail.get("decision")) == value_digest(expected), "independent_decision_mismatch")
        require(value_digest(detail.get("premises")) == value_digest(facts), "independent_premise_mismatch")
        require(value_digest(detail.get("context_evidence")) == value_digest(_context_evidence(context)), "independent_evidence_mismatch")
        require(detail.get("input_sha256") == doc.input_sha256 and detail.get("body_sha256") == text_digest(doc.input.text), "independent_input_binding")
        require(detail.get("policy_sha256") == POLICY_SHA256, "independent_detail_policy")
        require(all(type(detail.get(k)) is bool and detail[k] is False for k in FLAGS), "independent_permission_promotion")
        require(detail.get("body_only_grade_scoring_allowed") is False and detail.get("body_only_result") == "HOLD" and
                detail.get("valid_input_profile") == "body_context" and detail.get("real_world_premises_verified") is False and
                detail.get("reference_status") == "internally_fixed_conditional", "independent_authority_promotion")
        rows.append({"doc_id": doc_id, "input_sha256": doc.input_sha256,
                     "body_sha256": text_digest(doc.input.text), "visible_context_sha256": value_digest(context),
                     "facts_sha256": value_digest(facts), "expected": expected, "passed": True})
    require(value_digest([records, answers, details]) == before, "independent_input_mutation")
    counts = Counter(row["expected"]["reference_grade"] for row in rows)
    return {**FLAGS, "schema_version": "customer-reference-independent-audit-v1", "policy_sha256": POLICY_SHA256,
            "cases": len(rows), "passed": len(rows), "grade_counts": {g: counts[g] for g in GRADES},
            "oracle": "separate visible-context parser, explicit S/M tables and literal 27-grade table",
            "oracle_table_sha256": value_digest([{ "levels": list(k), "grade": v} for k, v in TRUTH_TABLE.items()]),
            "input_collection_sha256": before, "rows": rows, "source_input_mutated": False,
            "independent_real_world_truth_authority": False, "whole_prose_semantics_certified": False,
            "other_grade_exclusion_prose_semantics_checked": False, "model_accuracy": None,
            "limit": "Same selected internal policy, independently implemented; fictional assumptions remain unverified in reality."}


# Selected body-read proposals; neither grade nor arithmetic operator selects a
# member. Quote bindings make every proposal inspectable and drift-sensitive.
SEMANTIC_PROPOSALS = (
    ("identity-versus-retry-count", "원 사건/문서 식별자를 유지하면서 재시도·재전송 횟수와 고유 사건 수를 분리하는 업무 구조",
     (("retry-report", "재시도 횟수와 처리 문서 수가 섞여 보여 계산을 나눕니다."),
      ("event-sequence", "사건 수와 발송 횟수의 표 제목도 구분한다."))),
    ("page-correspondence-verification", "빈 쪽도 포함하는 원본 페이지 연결표를 기준으로 확인 완료와 실제 판본/번호 대응을 검증하는 업무 구조",
     (("translation-page", "빈 페이지도 문서의 페이지 번호에 포함했다."),
      ("page-fold-check", "표지 안쪽의 빈 쪽도 번호 체계에 포함했다."))),
    ("scan-transform-versus-missing-content", "스캔 방향·범위를 고치는 것과 잘린 원문 내용 복구를 구분하고 원본 확인이나 재스캔을 요구하는 업무 구조",
     (("scan-rotate", "회전만으로 글자가 잘린 부분이 복구되지는 않으므로 잘림은 재스캔 목록에 따로 적는다."),
      ("scanner-crop", "연속 스캔 전에 한 장을 먼저 저장해 글자 잘림을 점검합니다."))),
    ("received-versus-usable-inventory", "실물/외관 수량과 검사·건조 후 사용 가능 수량을 별도 상태로 유지하고 후속 입고를 기존 계수와 구분하는 업무 구조",
     (("receiving-shortfall", "실제 사용 가능한 수량은 외관 검사 후 따로 갱신한다."),
      ("tray-verification", "외관 정상과 즉시 사용 가능은 서로 다른 칸에 표시했다."))),
)


def semantic_family_candidates(records):
    docs = validate_documents(records)
    by_family = {}
    for doc in docs:
        by_family.setdefault(doc.family_id, []).append(doc)
    proposals, skipped = [], []
    for name, reason, selected in SEMANTIC_PROPOSALS:
        members = []
        for key, quote in selected:
            matches = by_family.get("family-" + key, [])
            require(len(matches) <= 1, "independent_semantic_family_ambiguous")
            if not matches:
                continue
            doc = matches[0]
            require(doc.input.text.count(quote) == 1, "independent_semantic_quote_drift")
            start = doc.input.text.index(quote)
            members.append({"doc_id": doc.input.doc_id, "family_id": doc.family_id,
                            "body_sha256": text_digest(doc.input.text), "quote": quote,
                            "start": start, "end": start + len(quote), "quote_sha256": text_digest(quote)})
        if len(members) < 2:
            skipped.append(name)
            continue
        proposals.append({"name": name, "reason": reason, "members": members})
    return {**FLAGS, "schema_version": "customer-reference-semantic-candidates-v1",
            "status": "additional_authored_grouping_candidates_not_approved", "proposals": proposals,
            "candidate_groups": len(proposals), "candidate_member_documents": len({m["doc_id"] for p in proposals for m in p["members"]}),
            "skipped_absent_groups": skipped, "frozen_metadata_changed": False, "split_rules_changed": False,
            "semantic_independence_certified": False, "exhaustive_review_performed": False,
            "limit": "Four selected workflow patterns; not a blanket grouping by arithmetic operator or a complete semantic audit."}
