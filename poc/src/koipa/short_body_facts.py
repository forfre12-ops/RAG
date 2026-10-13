"""Bounded text-fact parser for nine short reference forms, NOT general NLP.

Only the supplied text is accepted. No context, expected grade or target facts.
Every character is consumed. Unsupported forms never become empty/safe facts.
"""
from __future__ import annotations

import re

from koipa.policy_facts import require, text_digest, value_digest

VERSION = "short-body-facts-v1"
FLAGS = {"dataset_role": "policy_fixture", "training_allowed": False, "model_evaluation_allowed": False,
         "gold_eligible": False, "customer_accuracy_measured": False, "human_signoff_created": False}
NUMBER = r"(?:0|[1-9][0-9]{0,8}|[1-9][0-9]{0,2}(?:,[0-9]{3}){1,2})"
HEAD = r"[^\n]{1,80}\n"


def fact(name, value, evidence, *, state="observed", assertion=False):
    return {"name": name, "state": state, "value": value, "origin": "supplied_text",
            "interpretation": "document_assertion" if assertion else "not_determined" if state == "unknown" else "direct_value",
            "evidence": evidence}


def _evidence(text, match, *groups):
    return [{"start": match.start(g), "end": match.end(g), "sha256": text_digest(match.group(g))} for g in groups]


def _int(match, key):
    return int(match[key].replace(",", ""))


def _result(text, kind, facts):
    return {**FLAGS, "schema_version": VERSION, "document_sha256": text_digest(text), "status": "supported_bounded_form",
            "supported_form": kind, "facts": facts, "facts_sha256": value_digest(facts),
            "scope": "entire_supplied_text_only", "real_document_completeness_verified": False,
            "general_natural_language_supported": False}


def extract_facts(text):
    require(isinstance(text, str) and 0 < len(text) <= 20000, "short_body_input_invalid")

    def match(pattern):
        return re.fullmatch(HEAD + pattern + r"\n", text)

    m = match(r"(?P<domain>입력 x는 (?P<lo>[0-9]{1,3}) 이상 (?P<hi>[0-9]{1,3}) 이하의 정수다\.) "
              r"(?P<ops>먼저 x에 (?P<a>[0-9]{1,3})을 곱하고 (?P<b>[0-9]{1,3})을 더한다\. "
              r"합을 (?P<mod>[1-9][0-9]{0,2})로 나눈 나머지를 출력한다\.)\n"
              r"(?P<out>출력은 정수 하나다\.) (?P<tests>x=0이면 (?P<y0>[0-9]{1,3}), x=1이면 (?P<y1>[0-9]{1,3}), "
              r"x=2이면 (?P<y2>[0-9]{1,3})이어야 한다\.) (?P<state>별도 상태값은 사용하지 않는다\.)")
    if m:
        return _result(text, "function", [
            fact("input_domain", {"symbol": "x", "type": "integer", "min": _int(m, "lo"), "max": _int(m, "hi")}, _evidence(text, m, "domain")),
            fact("program", [{"op": "multiply", "value": _int(m, "a")}, {"op": "add", "value": _int(m, "b")},
                             {"op": "modulo", "value": _int(m, "mod")}], _evidence(text, m, "ops")),
            fact("output_type", "integer", _evidence(text, m, "out")),
            fact("test_vectors", [{"x": i, "y": _int(m, "y" + str(i))} for i in range(3)], _evidence(text, m, "tests")),
            fact("state_statement", "no_separate_state", _evidence(text, m, "state"), assertion=True)])
    m = match(r"(?P<observations>입력 x=0, 1, 2를 순서대로 시험했다\. 관측 출력은 각각 (?P<y0>[0-9]{1,3}), "
              r"(?P<y1>[0-9]{1,3}), (?P<y2>[0-9]{1,3})이다\.)\n"
              r"(?P<omission>내부 계산 순서와 나머지 연산의 기준값은 이번 기록에 포함하지 않았다\.) "
              r"(?P<next>다음 회차에는 x=(?P<n>[0-9]{1,3})을 측정한다\.)")
    if m:
        return _result(text, "measurement", [
            fact("observations", [{"x": i, "y": _int(m, "y" + str(i))} for i in range(3)], _evidence(text, m, "observations")),
            fact("omission_statement", ["operation_order", "modulus"], _evidence(text, m, "omission"), assertion=True),
            fact("program", None, _evidence(text, m, "observations", "omission"), state="unknown"),
            fact("next_input", _int(m, "n"), _evidence(text, m, "next"))])
    m = match(r"(?P<slot>시험실 (?P<room>[A-Z])의 작업대 (?P<bench>[0-9]{1,2})번을 (?P<day>[월화수목금토일]요일) "
              r"(?P<start>[0-9]{1,2})시부터 (?P<end>[0-9]{1,2})시까지 사용한다\.) "
              r"(?P<roles>실행 담당은 (?P<executor>[가-힣]{1,12}), 결과 취합은 (?P<aggregator>[가-힣]{1,12})이다\.)\n"
              r"작업 종료 후 단말 전원을 끄고 사용 시간을 시설대장에 적는다\. "
              r"(?P<omission>검산 수식과 측정값은 이 일정표에 넣지 않는다\.)")
    if m:
        return _result(text, "slot", [
            fact("work_slot", {"room": m["room"], "bench": _int(m, "bench"), "weekday": m["day"],
                               "start_hour": _int(m, "start"), "end_hour": _int(m, "end")}, _evidence(text, m, "slot")),
            fact("work_roles", {"execution": m["executor"], "aggregation": m["aggregator"]}, _evidence(text, m, "roles")),
            fact("omission_statement", ["formula", "measurement_values"], _evidence(text, m, "omission"), assertion=True)])
    m = match(r"(?P<item>가상 품목 (?P<item_code>[A-Z][0-9])의 회당 납품량은 (?P<qty>[0-9]{1,6})개다\.) "
              r"(?P<prices>최초 제시 단가는 (?P<offer>" + NUMBER + r")원, 양보 가능한 최저 단가는 (?P<floor>" + NUMBER + r")원이다\.)\n"
              r"(?P<condition>(?P<days>[0-9]{1,3})일 안에 대금 전액을 지급하는 조건이면 운송비를 (?P<payer>공급자|구매자)가 부담한다\.) "
              r"(?P<installment>분할 지급 요청에는 단가를 (?P<reduce>낮추지 않는다|낮춘다)\.)")
    if m:
        return _result(text, "negotiation", [
            fact("delivery_item", {"code": m["item_code"], "quantity": _int(m, "qty"), "unit": "개", "basis": "per_delivery"}, _evidence(text, m, "item")),
            fact("quoted_unit_price", {"amount": _int(m, "offer"), "currency": "KRW", "basis": "per_item"}, _evidence(text, m, "prices")),
            fact("floor_unit_price", {"amount": _int(m, "floor"), "currency": "KRW", "basis": "per_item"}, _evidence(text, m, "prices")),
            fact("freight_condition", {"if": {"payment": "full", "within_days": _int(m, "days")},
                                        "then": {"freight_payer": "supplier" if m["payer"] == "공급자" else "buyer"}}, _evidence(text, m, "condition")),
            fact("installment_condition", {"if": "installment_request", "price_reduction_allowed": m["reduce"] == "낮춘다"}, _evidence(text, m, "installment"))])
    m = match(r"(?P<receipt>포장 샘플 (?P<qty>[0-9]{1,3})상자를 (?P<day>[월화수목금토일]요일) (?P<hour>[0-9]{1,2})시에 "
              r"(?P<place>[가-힣]+ 접수대)로 받는다\.) (?P<roles>수량 확인은 (?P<count_team>[가-힣]+), "
              r"빈 포장 회수는 (?P<return_team>[가-힣]+)이 맡는다\.)\n"
              r"(?P<completion>확인이 끝나면 접수번호 (?P<code>[A-Z]-[0-9]{1,3})로 완료 상태를 기록한다\.) "
              r"(?P<omission>단가, 계약 상대와 협상 조건은 이 기록에 없다\.)")
    if m:
        return _result(text, "receipt", [
            fact("receipt", {"quantity": _int(m, "qty"), "unit": "상자", "weekday": m["day"], "hour": _int(m, "hour"), "place": m["place"]}, _evidence(text, m, "receipt")),
            fact("receipt_roles", {"quantity_check": m["count_team"], "empty_package_collection": m["return_team"]}, _evidence(text, m, "roles")),
            fact("completion", {"receipt_id": m["code"], "state": "완료", "after": "확인"}, _evidence(text, m, "completion")),
            fact("omission_statement", ["price", "counterparty", "negotiation_conditions"], _evidence(text, m, "omission"), assertion=True)])
    m = match(r"(?P<assignments>(?P<day>[월화수목금토일]요일) 오전에는 설비 (?P<equipment>[A-Z])를 (?P<morning>[가-힣]+)이 사용하고 "
              r"오후에는 (?P<afternoon>[가-힣]+)이 사용한다\.) (?P<check>정리 확인은 (?P<hour>[0-9]{1,2})시에 진행한다\.)\n"
              r"사용 종료 시 바닥의 이동 표식을 원위치로 돌리고 완료 여부를 안내판에 표시한다\.")
    if m:
        return _result(text, "allocation", [
            fact("assignments", [{"weekday": m["day"], "period": period, "equipment": m["equipment"], "team": m[key]}
                                 for period, key in (("오전", "morning"), ("오후", "afternoon"))], _evidence(text, m, "assignments")),
            fact("check_hour", _int(m, "hour"), _evidence(text, m, "check"))])
    m = match(r"(?P<transfer>(?P<day>[월화수목금토일]요일)에 빈 보관함 (?P<qty>[0-9]{1,3})개를 (?P<floor>[0-9]{1,2})층 "
              r"(?P<place>[가-힣]+)로 옮긴다\.) (?P<roles>(?P<count_team>[가-힣]+)이 개수를 적고 "
              r"(?P<lock_team>[가-힣]+)이 바퀴 잠금 상태를 확인한다\.)\n"
              r"누락이 있으면 다음 운반 전에 준비실 안내판에 남긴다\. (?P<omission>개별 인명과 연락처는 기록하지 않는다\.)")
    if m:
        return _result(text, "handover", [
            fact("transfer", {"weekday": m["day"], "quantity": _int(m, "qty"), "unit": "개", "item": "빈 보관함",
                              "floor": _int(m, "floor"), "place": m["place"]}, _evidence(text, m, "transfer")),
            fact("transfer_roles", {"count_record": m["count_team"], "wheel_lock_check": m["lock_team"]}, _evidence(text, m, "roles")),
            fact("omission_statement", ["individual_names", "contacts"], _evidence(text, m, "omission"), assertion=True)])
    for kind, labels, tail in (
        ("receipt_form", ("접수번호", "품목", "수량", "확인 부서"), "수량을 확인한 뒤 누락 항목을 적는 공통 양식이다. 모든 기입란은 비어 있다."),
        ("work_form", ("작업일", "장소", "할 일", "확인 부서"), "작업을 마친 뒤 확인 부서를 기입하는 공통 양식이다. 작성된 작업값은 없다."),
    ):
        pattern = "".join(re.escape(label) + r": (?P<v" + str(i) + r">[^\n]{0,80})\n" for i, label in enumerate(labels))
        m = match(r"(?P<fields>" + pattern + r")(?P<claim>" + re.escape(tail) + r")")
        if m:
            fields = [{"label": label, "raw": m["v" + str(i)], "value": None if re.fullmatch(r"_+", m["v" + str(i)]) else m["v" + str(i)],
                       "kind": "placeholder" if re.fullmatch(r"_+", m["v" + str(i)]) else "text"} for i, label in enumerate(labels)]
            return _result(text, kind, [fact("form_fields", fields, _evidence(text, m, "fields")),
                                       fact("form_empty_statement", True, _evidence(text, m, "claim"), assertion=True)])
    return {**FLAGS, "schema_version": VERSION, "document_sha256": text_digest(text), "status": "unsupported_body",
            "facts": [], "fixed": False, "scope": "entire_supplied_text_only"}


def semantic_checks(extracted):
    """Checks relationships, not business value, confidentiality or document grade."""
    require(extracted.get("status") == "supported_bounded_form", "unsupported_body_for_semantics")
    values = {f["name"]: f["value"] for f in extracted["facts"]}
    warnings, checks = [], {}
    if values.get("program"):
        domain, ops = values["input_domain"], values["program"]
        lo, hi = domain["min"], domain["max"]
        require(0 <= lo <= hi <= 999, "short_function_domain_invalid")
        outputs = {}
        for x in range(lo, hi + 1):
            y = x
            for op in ops:
                y = y * op["value"] if op["op"] == "multiply" else y + op["value"] if op["op"] == "add" else y % op["value"]
            outputs[str(x)] = y
        vectors_ok = all(str(r["x"]) in outputs and outputs[str(r["x"])] == r["y"] for r in values["test_vectors"])
        if not vectors_ok:
            warnings.append("test_vectors_contradict_program")
        checks["finite_function"] = {"domain": [lo, hi], "outputs": outputs, "test_vectors_consistent": vectors_ok,
                                     "real_product_reproduction_proven": False}
    if "observations" in values:
        observed = {r["x"]: r["y"] for r in values["observations"]}
        unseen = values["next_input"]
        if unseen not in observed:
            checks["non_uniqueness"] = {"observations": observed, "witness_input": unseen,
                "candidate_a": {**observed, unseen: 0}, "candidate_b": {**observed, unseen: 1},
                "meaning": "Two finite lookup functions agree on all observed pairs, disagree on the announced next input; neither is the answer."}
    if "quoted_unit_price" in values:
        if values["floor_unit_price"]["amount"] > values["quoted_unit_price"]["amount"]:
            warnings.append("floor_exceeds_offer")
        checks["negotiation"] = {"price_roles_distinct": True, "condition_is_one_way_implication": True,
                                  "real_contract_validity_proven": False}
    if "form_fields" in values:
        empty = all(r["kind"] == "placeholder" for r in values["form_fields"])
        if values["form_empty_statement"] and not empty:
            warnings.append("empty_statement_conflicts_with_filled_field")
        checks["form"] = {"all_supplied_fields_placeholders": empty, "missing_pages_verified": False}
    if "work_slot" in values:
        slot = values["work_slot"]
        if not 0 <= slot["start_hour"] < slot["end_hour"] <= 24:
            warnings.append("work_slot_time_inconsistent")
    for key in ("receipt",):
        if key in values and not 0 <= values[key]["hour"] <= 23:
            warnings.append("hour_out_of_range")
    if "check_hour" in values and not 0 <= values["check_hour"] <= 23:
        warnings.append("hour_out_of_range")
    return {"checks": checks, "warnings": warnings, "grade_determined": False}
