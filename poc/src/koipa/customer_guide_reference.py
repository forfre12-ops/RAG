"""Offline synthetic reference: PDF pp11-12 plus explicitly local V anchors.

No inference, permission promotion, production formula or legal determination.
An authored fictional context is a premise, not an independently observed fact.
"""
from __future__ import annotations

import copy
from itertools import product

from koipa.customer_benchmark import FLAGS, GRADES, validate_answers, validate_documents
from koipa.policy_facts import require, text_digest, value_digest

POLICY = {
    "id": "customer-guide-synthetic-reference", "version": "0.1",
    "selection": "user_selected_guide_based_internal_trial",
    "source": {"path": "doc/[참고] 영업비밀 등급분류 가이드.pdf",
               "sha256": "c3a0b9cbc971ced27945c13a3bc1ae8d5f6165fdac128646369c8af62a191fcb",
               "criteria_page": 11, "formula_page": 12},
    "formula": {"0": "S3", "1": "S2", "2": "S2", "4": "S1", "8": "TS"},
    "local_anchors_not_from_pdf": {
        "v1_max_person_hours": 40, "v1_max_cost_krw": 1000000,
        "v2_min_person_hours": 480, "v2_min_cost_krw": 30000000,
        "between_anchors": "HOLD", "any_required_unknown": "HOLD",
        "conflicting_premises": "HOLD", "scope": "cost and effort attributable to this document's information only"},
    "grade_aliases": {"TS": "4단계 곱8 극비", "S1": "4단계 곱4 비밀",
                      "S2": "4단계 곱1/2 대외비", "S3": "4단계 곱0 일반공개정보"},
    "not_selected": ["p5 five-factor sum", "p12 two/three-level formulas", "v22", "fnr"],
    "customer_protocol_accepted": False, "production_policy_changed": False,
    "legal_or_public_release_authorization": False,
}
POLICY_SHA256 = value_digest(POLICY)

# The model sees facts, never factor scores, grade, rules or authoring identities.
# Every field is explicitly given; absent fields are not implicitly false.
FIELDS = {
    "public_exact_body": ("reader_scope", "해당 판본 전체의 외부 공개", bool),
    "obtainable_without_holder": ("reader_scope", "보유자를 거치지 않은 취득 가능", bool),
    "ordinary_access_difficult": ("reader_scope", "통상 취득의 어려움", bool),
    "cost_krw": ("impact_description", "해당 정보 취득개발 비용(원)", int),
    "person_hours": ("impact_description", "해당 정보 취득개발 투입(인시)", int),
    "economic_utility": ("impact_description", "경제적 활용 가능", bool),
    "investment_scope_exact": ("impact_description", "투입 기록의 해당 정보 귀속 확인", bool),
    "secrecy_manageable": ("management_controls", "비밀 유지 가능", bool),
    "all_staff_knows": ("management_controls", "전 직원이 알아야 함", bool),
    "business_need_only": ("management_controls", "업무 필요자만 열람", bool),
    "individual_approval": ("management_controls", "개별 승인자만 열람", bool),
    "access_enforced": ("management_controls", "접근 제한 실제 적용", bool),
    "release_authorized": ("release_authorized", "공개 허가", bool),
    "other_risk_present": ("other_risk_present", "다른 보호 사유", bool),
}
REQUIRED = tuple(k for k in FIELDS if k not in {"release_authorized", "other_risk_present"})
RULE_IDS = {*(f"guide-s-{i}" for i in range(3)), *(f"trial-v-{i}" for i in range(3)),
            *(f"guide-m-{i}" for i in range(3)), "guide-product-four-level"}


def validate_premises(facts):
    require(isinstance(facts, dict) and set(facts) == set(FIELDS), "guide_fact_fields_invalid")
    for key, (_, _, typ) in FIELDS.items():
        v = facts[key]
        require(v is None or type(v) is typ, "guide_fact_type_invalid")
        if typ is int and v is not None:
            require(0 <= v <= 10**12, "guide_fact_range_invalid")
    return facts


def encode_context(facts):
    validate_premises(facts)
    result = []
    for group in ("reader_scope", "impact_description", "management_controls"):
        parts = []
        for key, (g, title, _) in FIELDS.items():
            if g == group:
                v = facts[key]
                rendered = "미수신" if v is None else ("예" if v is True else "아니오" if v is False else str(v))
                parts.append(f"{title}: {rendered}")
        result.append({"name": group, "value": "; ".join(parts), "origin": "synthetic_assumption"})
    result.extend({"name": key, "value": facts[key], "origin": "synthetic_assumption"}
                  for key in ("release_authorized", "other_risk_present"))
    return result


def decode_context(context):
    """Parse all model-visible premises, never read factor scores from sidecars."""
    require(isinstance(context, list) and len(context) == 5, "guide_context_invalid")
    by_name = {r["name"]: r for r in context}
    require(set(by_name) == {g for g, _, _ in FIELDS.values()}, "guide_context_groups_invalid")
    facts = {}
    for group in ("reader_scope", "impact_description", "management_controls"):
        require(type(by_name[group]["value"]) is str, "guide_context_string_required")
        entries = [p.split(": ") for p in by_name[group]["value"].split("; ")]
        require(all(len(p) == 2 for p in entries) and len({p[0] for p in entries}) == len(entries), "guide_context_entries_invalid")
        labels = dict(entries)
        expected = {t for g, t, _ in FIELDS.values() if g == group}
        require(set(labels) == expected, "guide_context_labels_invalid")
        for key, (g, title, typ) in FIELDS.items():
            if g != group:
                continue
            s = labels[title]
            if s == "미수신":
                facts[key] = None
            elif typ is bool:
                require(s in {"예", "아니오"}, "guide_context_bool_invalid")
                facts[key] = s == "예"
            else:
                require(s.isascii() and s.isdecimal() and str(int(s)) == s, "guide_context_integer_invalid")
                facts[key] = int(s)
    for key in ("release_authorized", "other_risk_present"):
        facts[key] = by_name[key]["value"]
    validate_premises(facts)
    require(value_digest(context) == value_digest(encode_context(facts)), "guide_context_canonical_mismatch")
    return facts


def grade_from_levels(s, v, m):
    require(all(type(x) is int and x in (0, 1, 2) for x in (s, v, m)), "guide_level_invalid")
    return POLICY["formula"][str(s * v * m)]


def _factor_levels(f):
    reasons = []
    s = v = m = None
    if f["public_exact_body"] is True:
        if f["obtainable_without_holder"] is True and f["ordinary_access_difficult"] is False:
            s = 0
        else:
            reasons.append("conflicting_public_access")
    elif f["public_exact_body"] is False:
        if f["obtainable_without_holder"] is False and f["ordinary_access_difficult"] is True:
            s = 2
        elif f["obtainable_without_holder"] is True and f["ordinary_access_difficult"] is True:
            s = 1
        else:
            reasons.append("secrecy_outside_clear_anchors")
    if f["investment_scope_exact"] is not True:
        reasons.append("investment_not_attributable")
    elif f["economic_utility"] is False:
        if f["cost_krw"] == f["person_hours"] == 0:
            v = 0
        else:
            reasons.append("conflicting_value_premises")
    elif f["economic_utility"] is True:
        cost, hours = f["cost_krw"], f["person_hours"]
        a = POLICY["local_anchors_not_from_pdf"]
        if cost >= a["v2_min_cost_krw"] or hours >= a["v2_min_person_hours"]:
            v = 2
        elif 0 < cost + hours and cost <= a["v1_max_cost_krw"] and hours <= a["v1_max_person_hours"]:
            v = 1
        else:
            reasons.append("value_outside_clear_anchors")
    if f["all_staff_knows"] is True or f["secrecy_manageable"] is False:
        if f["individual_approval"] is False and f["business_need_only"] is False and f["access_enforced"] is False:
            m = 0
        else:
            reasons.append("conflicting_management_premises")
    elif f["secrecy_manageable"] is True and f["all_staff_knows"] is False:
        if f["access_enforced"] is True and f["business_need_only"] is True:
            m = 2 if f["individual_approval"] is True else 1
        else:
            reasons.append("management_not_demonstrated")
    return {"S": s, "V": v, "M": m}, reasons


def decide(facts):
    f = validate_premises(facts)
    missing = [k for k in REQUIRED if f[k] is None]
    # No zero-product short circuit: a missing factor is not a proven zero.
    levels, reasons = ({"S": None, "V": None, "M": None}, ["required_evidence_unknown"]) if missing else _factor_levels(f)
    fixed = not reasons and all(x is not None for x in levels.values())
    grade = grade_from_levels(*levels.values()) if fixed else None
    rules = [f"guide-s-{levels['S']}", f"trial-v-{levels['V']}", f"guide-m-{levels['M']}", "guide-product-four-level"] if fixed else []
    return {"status": "conditional_reference" if fixed else "HOLD", "reference_grade": grade,
            "factors": levels, "product": levels["S"] * levels["V"] * levels["M"] if fixed else None,
            "rule_ids": rules, "missing_evidence": missing, "reasons": reasons,
            "release_authorized_in_scenario": f["release_authorized"],
            "separate_disclosure_review_required": f["release_authorized"] is not True or f["other_risk_present"] is not False,
            "disclosure_permission_granted_by_classifier": False}


def evidence_for_context(context):
    return [{"name": key, "origin": "synthetic_assumption", "pointer": f"/context/{i}/value",
             "quote": row["value"], "sha256": value_digest(row["value"])}
            for key, (group, _, _) in FIELDS.items() for i, row in enumerate(context) if row["name"] == group]


def build_case(draft, facts, rationale):
    """New contextual version; retain the body and its family, never mutate drafts."""
    validate_documents([draft])
    require(type(rationale) is str and len(rationale) >= 40, "guide_rationale_required")
    record = copy.deepcopy(draft)
    context = encode_context(facts)
    record["input"]["context"] = context
    record["input"]["doc_id"] = "doc-" + value_digest({"text": record["input"]["text"], "context": context})[:24]
    record["input_sha256"] = value_digest(record["input"])
    decision = decide(decode_context(context))
    d = validate_documents([record])[0]
    answer = None
    if decision["status"] == "conditional_reference":
        grade = decision["reference_grade"]
        answer = {"doc_id": d.input.doc_id, "input_sha256": d.input_sha256, "policy_id": POLICY["id"],
                  "policy_version": POLICY["version"], "policy_sha256": POLICY_SHA256,
                  "reference_grade": grade, "rule_ids": decision["rule_ids"],
                  "evidence_names": [c.name for c in d.claims],
                  "other_grade_exclusions": {g: f"확정 가정에서 곱은 {decision['product']}이며 {g}의 허용 곱 {','.join(p for p, v in POLICY['formula'].items() if v == g)}과 다름."
                                             for g in GRADES if g != grade}, "status": "authored_candidate"}
    detail = {**FLAGS, "doc_id": d.input.doc_id, "input_sha256": d.input_sha256,
              "policy_sha256": POLICY_SHA256, "premises": facts, "context_evidence": evidence_for_context(context),
              "decision": decision, "rationale": rationale, "rationale_origin": "authored_explanation_not_independent_review",
              "body_sha256": text_digest(d.input.text), "parent_draft_id": draft["input"]["doc_id"],
              "reference_status": "internally_fixed_conditional" if answer else "not_fixed",
              "valid_input_profile": "body_context", "body_only_grade_scoring_allowed": False,
              "body_only_result": "HOLD", "real_world_premises_verified": False}
    return record, answer, detail


def validate_reference(records, answers, details):
    docs = validate_documents(records)
    candidates = validate_answers(docs, answers)
    require(type(details) is list and len(details) == len(docs), "guide_detail_count_invalid")
    by_id = {r["doc_id"]: r for r in details}
    require(len(by_id) == len(docs) and set(by_id) == {d.input.doc_id for d in docs}, "guide_detail_ids_invalid")
    gold = {a.doc_id: a for a in candidates}
    for d in docs:
        detail, a = by_id[d.input.doc_id], gold[d.input.doc_id]
        for k in FLAGS:
            require(type(detail[k]) is bool and detail[k] is False, "guide_detail_permission_invalid")
        f = decode_context([c.model_dump() for c in d.input.context])
        computed = decide(f)
        require(value_digest(detail["premises"]) == value_digest(f), "guide_premises_mismatch")
        require(detail["input_sha256"] == d.input_sha256 and detail["body_sha256"] == text_digest(d.input.text), "guide_detail_input_mismatch")
        require(detail["policy_sha256"] == a.policy_sha256 == POLICY_SHA256 and a.policy_id == POLICY["id"] and
                a.policy_version == POLICY["version"], "guide_policy_mismatch")
        require(value_digest(detail["context_evidence"]) == value_digest(evidence_for_context([c.model_dump() for c in d.input.context])), "guide_context_evidence_mismatch")
        require(value_digest(computed) == value_digest(detail["decision"]) and computed["reference_grade"] == a.reference_grade and
                computed["rule_ids"] == a.rule_ids, "guide_answer_recompute_mismatch")
        require(detail["body_only_grade_scoring_allowed"] is False and detail["valid_input_profile"] == "body_context" and
                detail["body_only_result"] == "HOLD" and detail["real_world_premises_verified"] is False and
                detail["reference_status"] == "internally_fixed_conditional", "guide_authority_mismatch")
    return docs


def formula_audit():
    # Independent combinatorial oracle, no product-map lookup in its calculation.
    rows = []
    for s, v, m in product(range(3), repeat=3):
        expected = "S3" if 0 in (s, v, m) else {0: "S2", 1: "S2", 2: "S1", 3: "TS"}[sum(x == 2 for x in (s, v, m))]
        actual = grade_from_levels(s, v, m)
        require(expected == actual, "guide_formula_oracle_mismatch")
        rows.append({"S": s, "V": v, "M": m, "expected": expected, "actual": actual})
    return {"combinations": rows, "passed": 27, "model_accuracy": None, "oracle": "zero presence then count of twos"}
