"""Hand-authored short fictional drafts for input observability, NOT fixed truth.

No production data, no real credentials, no model classification/training. Candidate
labels are DESIGN HYPOTHESES; this module does not certify natural-language grades.
"""
from __future__ import annotations

import copy

from koipa.policy_facts import text_digest, value_digest

VERSION = "short-reference-input-design-v0.2-draft"
FLAGS = {"dataset_role": "policy_fixture", "training_allowed": False, "model_evaluation_allowed": False,
         "gold_eligible": False, "customer_accuracy_measured": False, "human_signoff_created": False}

# Three genres, nine source texts. Do not count paired contexts as independent documents.
DRAFTS = (
    ("technical", "function", "TS", "R-CORE", "가람 처리기 검산 메모\n"
     "입력 x는 0 이상 9 이하의 정수다. 먼저 x에 7을 곱하고 3을 더한다. 합을 11로 나눈 나머지를 출력한다.\n"
     "출력은 정수 하나다. x=0이면 3, x=1이면 10, x=2이면 6이어야 한다. 별도 상태값은 사용하지 않는다.\n",
     ["입력 x는 0 이상 9 이하의 정수다.", "먼저 x에 7을 곱하고 3을 더한다.", "합을 11로 나눈 나머지를 출력한다.",
      "x=0이면 3, x=1이면 10, x=2이면 6이어야 한다."]),
    ("technical", "measurement", "S1", "R-DETAIL", "가람 처리기 검산 메모\n"
     "입력 x=0, 1, 2를 순서대로 시험했다. 관측 출력은 각각 3, 10, 6이다.\n"
     "내부 계산 순서와 나머지 연산의 기준값은 이번 기록에 포함하지 않았다. 다음 회차에는 x=3을 측정한다.\n",
     ["입력 x=0, 1, 2를 순서대로 시험했다.", "관측 출력은 각각 3, 10, 6이다.",
      "내부 계산 순서와 나머지 연산의 기준값은 이번 기록에 포함하지 않았다."]),
    ("technical", "slot", "S2", "R-OPS", "가람 처리기 검산 메모\n"
     "시험실 B의 작업대 2번을 화요일 14시부터 16시까지 사용한다. 실행 담당은 분석반, 결과 취합은 운영반이다.\n"
     "작업 종료 후 단말 전원을 끄고 사용 시간을 시설대장에 적는다. 검산 수식과 측정값은 이 일정표에 넣지 않는다.\n",
     ["시험실 B의 작업대 2번을 화요일 14시부터 16시까지 사용한다.", "실행 담당은 분석반, 결과 취합은 운영반이다."]),
    ("commercial", "negotiation", "S1", "R-DETAIL", "나루 납품 검토 기록\n"
     "가상 품목 Q7의 회당 납품량은 400개다. 최초 제시 단가는 14,500원, 양보 가능한 최저 단가는 12,800원이다.\n"
     "30일 안에 대금 전액을 지급하는 조건이면 운송비를 공급자가 부담한다. 분할 지급 요청에는 단가를 낮추지 않는다.\n",
     ["최초 제시 단가는 14,500원, 양보 가능한 최저 단가는 12,800원이다.",
      "30일 안에 대금 전액을 지급하는 조건이면 운송비를 공급자가 부담한다."]),
    ("commercial", "receipt", "S2", "R-OPS", "나루 납품 검토 기록\n"
     "포장 샘플 4상자를 수요일 10시에 동측 접수대로 받는다. 수량 확인은 자재반, 빈 포장 회수는 지원반이 맡는다.\n"
     "확인이 끝나면 접수번호 N-42로 완료 상태를 기록한다. 단가, 계약 상대와 협상 조건은 이 기록에 없다.\n",
     ["포장 샘플 4상자를 수요일 10시에 동측 접수대로 받는다.", "접수번호 N-42로 완료 상태를 기록한다."]),
    ("commercial", "blank", "S3", "R-BLANK", "나루 납품 검토 기록\n"
     "접수번호: ______\n품목: ______\n수량: ______\n확인 부서: ______\n"
     "수량을 확인한 뒤 누락 항목을 적는 공통 양식이다. 모든 기입란은 비어 있다.\n",
     ["접수번호: ______", "품목: ______", "수량: ______", "확인 부서: ______"]),
    ("operations", "allocation", "S2", "R-OPS", "다온 작업 안내\n"
     "월요일 오전에는 설비 A를 점검반이 사용하고 오후에는 포장반이 사용한다. 정리 확인은 17시에 진행한다.\n"
     "사용 종료 시 바닥의 이동 표식을 원위치로 돌리고 완료 여부를 안내판에 표시한다.\n",
     ["월요일 오전에는 설비 A를 점검반이 사용하고 오후에는 포장반이 사용한다.", "정리 확인은 17시에 진행한다."]),
    ("operations", "handover", "S2", "R-OPS", "다온 작업 안내\n"
     "수요일에 빈 보관함 6개를 2층 준비실로 옮긴다. 인계반이 개수를 적고 시설반이 바퀴 잠금 상태를 확인한다.\n"
     "누락이 있으면 다음 운반 전에 준비실 안내판에 남긴다. 개별 인명과 연락처는 기록하지 않는다.\n",
     ["수요일에 빈 보관함 6개를 2층 준비실로 옮긴다.", "시설반이 바퀴 잠금 상태를 확인한다."]),
    ("operations", "blank", "S3", "R-BLANK", "다온 작업 안내\n"
     "작업일: ______\n장소: ______\n할 일: ______\n확인 부서: ______\n"
     "작업을 마친 뒤 확인 부서를 기입하는 공통 양식이다. 작성된 작업값은 없다.\n",
     ["작업일: ______", "장소: ______", "할 일: ______", "확인 부서: ______"]),
)


def build_probes():
    inputs, annotations = [], []
    for family, name, private_grade, rule, text, quotes in DRAFTS:
        for visibility in ("private", "released"):
            context = {"origin": "synthetic_assumption", "scope_complete": True,
                       "private_current_revision": visibility == "private",
                       "release_authorized_for_this_revision": visibility == "released",
                       "current_core_asset": name == "function", "other_high_risk_material": False,
                       "world": "fictional; no real persons, access secrets or customer assets"}
            raw = {"doc_id": "sp-" + value_digest({"text": text, "context": context})[:20],
                   "text": text, "document_sha256": text_digest(text), "context": context}
            requirements = []
            for index, quote in enumerate(quotes):
                start = text.index(quote)
                requirements.append({"id": f"B{index + 1}", "kind": "body", "start": start,
                                     "end": start + len(quote), "sha256": text_digest(quote)})
            for key in context:
                requirements.append({"id": "C-" + key, "kind": "context", "pointer": "/" + key,
                                     "value_sha256": value_digest(context[key])})
            expected = private_grade if visibility == "private" else "S3"
            inputs.append({**FLAGS, "input": raw})
            annotations.append({"doc_id": raw["doc_id"], "family_id": family, "source_text_id": family + "-" + name,
                "input_sha256": value_digest(raw), "policy_version": VERSION,
                "expectation_status": "design_hypothesis_not_fixed", "proposed_grade": expected,
                "proposed_status": "conditional_grade", "proposed_rule": rule if visibility == "private" else "R-RELEASED",
                "requirements": requirements, "requires_full_body": True,
                "business_value_proven": False, "grade_oracle_implemented": False})
        if name in {"function", "negotiation", "allocation"}:
            row, annotation = copy.deepcopy(inputs[-2]), copy.deepcopy(annotations[-2])
            row["input"]["context"]["scope_complete"] = None
            row["input"]["doc_id"] = "sp-" + value_digest(row["input"])[0:20]
            annotation.update(doc_id=row["input"]["doc_id"], input_sha256=value_digest(row["input"]),
                              proposed_grade=None, proposed_status="hold", proposed_rule="R-UNKNOWN")
            for req in annotation["requirements"]:
                if req.get("pointer") == "/scope_complete":
                    req["value_sha256"] = value_digest(None)
            inputs.append(row)
            annotations.append(annotation)
    return inputs, annotations
