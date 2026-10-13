"""Manually specified text-fact answer anchors. No primary parser or grade imports.

Independent implementation path, NOT an independent author or external reviewer.
Exact registered bodies only. Values are authored explicitly, not copied from the
extractor's output at build time.
"""
from __future__ import annotations

import copy

from koipa.policy_facts import require, text_digest


def _blank(labels):
    return [{"label": k, "raw": "______", "value": None, "kind": "placeholder"} for k in labels]


# (fact name, explicit reference value, exact supporting text). These contain no grades.
REFERENCE = {
    "4f79bc40d097b567c0f4d802947b29c4b0f8d81295ea97b6453e2978779601af": [
        ("input_domain", {"symbol": "x", "type": "integer", "min": 0, "max": 9}, "입력 x는 0 이상 9 이하의 정수다."),
        ("program", [{"op": "multiply", "value": 7}, {"op": "add", "value": 3}, {"op": "modulo", "value": 11}],
         "먼저 x에 7을 곱하고 3을 더한다. 합을 11로 나눈 나머지를 출력한다."),
        ("output_type", "integer", "출력은 정수 하나다."),
        ("test_vectors", [{"x": 0, "y": 3}, {"x": 1, "y": 10}, {"x": 2, "y": 6}], "x=0이면 3, x=1이면 10, x=2이면 6이어야 한다."),
        ("state_statement", "no_separate_state", "별도 상태값은 사용하지 않는다."),
    ],
    "7176b8eccb4b1559eb84a69ea22598807d17c0099d15ac69ef52c08601131e95": [
        ("observations", [{"x": 0, "y": 3}, {"x": 1, "y": 10}, {"x": 2, "y": 6}], "입력 x=0, 1, 2를 순서대로 시험했다. 관측 출력은 각각 3, 10, 6이다."),
        ("omission_statement", ["operation_order", "modulus"], "내부 계산 순서와 나머지 연산의 기준값은 이번 기록에 포함하지 않았다."),
        ("program", None, ["입력 x=0, 1, 2를 순서대로 시험했다. 관측 출력은 각각 3, 10, 6이다.",
                           "내부 계산 순서와 나머지 연산의 기준값은 이번 기록에 포함하지 않았다."]),
        ("next_input", 3, "다음 회차에는 x=3을 측정한다."),
    ],
    "c61dae5417daba54b0cfef640332ce83a5108af0782afd91dba8389cdb5a28f5": [
        ("work_slot", {"room": "B", "bench": 2, "weekday": "화요일", "start_hour": 14, "end_hour": 16},
         "시험실 B의 작업대 2번을 화요일 14시부터 16시까지 사용한다."),
        ("work_roles", {"execution": "분석반", "aggregation": "운영반"}, "실행 담당은 분석반, 결과 취합은 운영반이다."),
        ("omission_statement", ["formula", "measurement_values"], "검산 수식과 측정값은 이 일정표에 넣지 않는다."),
    ],
    "45d81d155cedad9e123ed2630ddb138be6f289a4935e51e10ceba53ad90fafc0": [
        ("delivery_item", {"code": "Q7", "quantity": 400, "unit": "개", "basis": "per_delivery"}, "가상 품목 Q7의 회당 납품량은 400개다."),
        ("quoted_unit_price", {"amount": 14500, "currency": "KRW", "basis": "per_item"}, "최초 제시 단가는 14,500원, 양보 가능한 최저 단가는 12,800원이다."),
        ("floor_unit_price", {"amount": 12800, "currency": "KRW", "basis": "per_item"}, "최초 제시 단가는 14,500원, 양보 가능한 최저 단가는 12,800원이다."),
        ("freight_condition", {"if": {"payment": "full", "within_days": 30}, "then": {"freight_payer": "supplier"}},
         "30일 안에 대금 전액을 지급하는 조건이면 운송비를 공급자가 부담한다."),
        ("installment_condition", {"if": "installment_request", "price_reduction_allowed": False}, "분할 지급 요청에는 단가를 낮추지 않는다."),
    ],
    "4fb63c145e1800a7e29c890ea3bf3c5858fc7afc563109d35aaf8441baaf0812": [
        ("receipt", {"quantity": 4, "unit": "상자", "weekday": "수요일", "hour": 10, "place": "동측 접수대"},
         "포장 샘플 4상자를 수요일 10시에 동측 접수대로 받는다."),
        ("receipt_roles", {"quantity_check": "자재반", "empty_package_collection": "지원반"}, "수량 확인은 자재반, 빈 포장 회수는 지원반이 맡는다."),
        ("completion", {"receipt_id": "N-42", "state": "완료", "after": "확인"}, "확인이 끝나면 접수번호 N-42로 완료 상태를 기록한다."),
        ("omission_statement", ["price", "counterparty", "negotiation_conditions"], "단가, 계약 상대와 협상 조건은 이 기록에 없다."),
    ],
    "ec7a387f9e10a814c6f84e516e2cf912713abe80c4ea055c397ef9a3cc62120f": [
        ("form_fields", _blank(("접수번호", "품목", "수량", "확인 부서")), "접수번호: ______\n품목: ______\n수량: ______\n확인 부서: ______\n"),
        ("form_empty_statement", True, "수량을 확인한 뒤 누락 항목을 적는 공통 양식이다. 모든 기입란은 비어 있다."),
    ],
    "93fb7761a35defa3b0951860f01342fabba3d1afeb672fae20063cb5edd1bfd6": [
        ("assignments", [{"weekday": "월요일", "period": "오전", "equipment": "A", "team": "점검반"},
                         {"weekday": "월요일", "period": "오후", "equipment": "A", "team": "포장반"}],
         "월요일 오전에는 설비 A를 점검반이 사용하고 오후에는 포장반이 사용한다."),
        ("check_hour", 17, "정리 확인은 17시에 진행한다."),
    ],
    "ef0e7fec70a93f47cc412aab8aa42147f519c491282ca179cec1b3c5c3695f3c": [
        ("transfer", {"weekday": "수요일", "quantity": 6, "unit": "개", "item": "빈 보관함", "floor": 2, "place": "준비실"},
         "수요일에 빈 보관함 6개를 2층 준비실로 옮긴다."),
        ("transfer_roles", {"count_record": "인계반", "wheel_lock_check": "시설반"}, "인계반이 개수를 적고 시설반이 바퀴 잠금 상태를 확인한다."),
        ("omission_statement", ["individual_names", "contacts"], "개별 인명과 연락처는 기록하지 않는다."),
    ],
    "e7f216f39c727180c63413a28af377c695d8d2274ee9b468e4250c848117ce99": [
        ("form_fields", _blank(("작업일", "장소", "할 일", "확인 부서")), "작업일: ______\n장소: ______\n할 일: ______\n확인 부서: ______\n"),
        ("form_empty_statement", True, "작업을 마친 뒤 확인 부서를 기입하는 공통 양식이다. 작성된 작업값은 없다."),
    ],
}


def anchored_answer(text):
    require(text_digest(text) in REFERENCE, "short_body_not_registered")
    result = []
    for name, value, quotes in REFERENCE[text_digest(text)]:
        anchors = [quotes] if isinstance(quotes, str) else quotes
        evidence = []
        for quote in anchors:
            require(text.count(quote) == 1, "reference_anchor_ambiguous_or_absent")
            start = text.index(quote)
            evidence.append({"start": start, "end": start + len(quote), "sha256": text_digest(quote)})
        unknown = name == "program" and value is None
        interpretation = "document_assertion" if name.endswith("statement") else "not_determined" if unknown else "direct_value"
        result.append({"name": name, "state": "unknown" if unknown else "observed", "value": copy.deepcopy(value),
                       "origin": "supplied_text", "interpretation": interpretation, "evidence": evidence})
    return result


def arithmetic_oracle(facts):
    """Repeated addition/subtraction, independent from primary multiply/modulo."""
    v = {f["name"]: f["value"] for f in facts}
    if not v.get("program"):
        return None
    a, b, modulus = (step["value"] for step in v["program"])
    output = {}
    for x in range(v["input_domain"]["min"], v["input_domain"]["max"] + 1):
        y = b
        for _ in range(a):
            y += x
        while y >= modulus:
            y -= modulus
        output[str(x)] = y
    return output
