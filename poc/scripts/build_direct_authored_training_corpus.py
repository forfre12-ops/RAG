"""Materialize the no-Qwen, directly-authored Proxy training corpus.

The corpus is deliberately training-only.  Its Korean scenario cards, document
forms, grade counterfactuals, and quality-audit rules are maintained in source;
there is no remote or local LLM call in this builder.  It is Proxy data, not
customer evidence, a golden evaluation set, or Locked Gold.
"""

from __future__ import annotations

from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from lloydk.proxy_corpus import validate_proxy_record
from scripts.assemble_proxy_training_pool import SELECTION_SEED, _balanced_quotas
from scripts.build_direct_authored_training_pilot import (
    CASES,
    GRADE_POLICY,
    _editorial_audit,
    _evidence_card,
)


OUT = ROOT / "datasets" / "proxy_gold" / "direct_authored_training_corpus.v6.jsonl"
MANIFEST = (
    ROOT / "datasets" / "proxy_gold" / "direct_authored_training_corpus.v6.manifest.json"
)

GRADE_VARIANTS = {"TS": 3, "S1": 3, "S2": 3, "S3": 2}
EXTRA_HIGH_GRADE_FAMILIES = 75

# Each work package is a separately authored operational purpose.  Combined
# with the 25 industry cases this yields 225 distinct document families.
WORK_PACKAGES = (
    {
        "id": "change-review",
        "title": "변경 적용 검토서",
        "purpose": "변경안이 현재 운영 조건과 충돌하지 않는지 확인하고 적용 범위를 확정",
        "records": "변경 요청서, 비교 결과표, 승인 의견, 롤백 조건",
        "trigger": "기존 기준을 그대로 적용하면 품질 또는 일정 영향이 발생할 가능성이 확인됨",
        "decision": "조건부 적용 여부와 재검토 시점을 결정",
    },
    {
        "id": "incident-followup",
        "title": "이상징후 후속조치 기록",
        "purpose": "반복되는 이상 징후의 원인 가설과 임시 조치의 효과를 대조",
        "records": "발생 이력, 원자료 대조표, 임시 조치 로그, 재발 확인 결과",
        "trigger": "관찰 지표가 관리 범위를 벗어나거나 같은 유형의 예외가 누적됨",
        "decision": "즉시 조치의 종료 여부와 근본 개선 과제를 결정",
    },
    {
        "id": "supplier-handoff",
        "title": "협력사 작업 전달 검토서",
        "purpose": "외부 협력사가 수행할 작업 범위와 전달 자료의 최소 범위를 정리",
        "records": "작업지시서, 납품 기준, 질의응답, 전달·회수 기록",
        "trigger": "내부 담당 업무 일부를 협력사 또는 현장 파트너와 분담해야 함",
        "decision": "전달 가능 범위와 책임 분담, 회수 확인 방법을 결정",
    },
    {
        "id": "monthly-performance",
        "title": "월간 운영성과 분석서",
        "purpose": "운영 지표의 변동 원인과 다음 기간의 조정 우선순위를 정리",
        "records": "월간 지표, 조건별 비교표, 예외 목록, 담당자 확인서",
        "trigger": "월간 성과가 목표 범위와 달라 원인별 분해가 필요함",
        "decision": "유지할 조건과 보완할 조건, 다음 관찰 주기를 결정",
    },
    {
        "id": "pilot-validation",
        "title": "현장 실증 검증 기록",
        "purpose": "제한된 현장 조건에서 변경안의 재현성과 적용 한계를 확인",
        "records": "시험 계획, 측정 로그, 사진·계측 요약, 결과 검토서",
        "trigger": "전면 적용 전 실제 작업 환경에서 검증 증적을 확보해야 함",
        "decision": "확대 적용·보완 시험·보류 중 하나를 결정",
    },
    {
        "id": "maintenance-plan",
        "title": "예방점검 계획 조정서",
        "purpose": "점검 순서와 자원 배치를 조정해 중단 위험을 낮출 방법을 검토",
        "records": "점검 이력, 설비 상태표, 자재 현황, 일정 조정안",
        "trigger": "설비 상태 또는 작업 일정 변화로 기존 점검 주기를 재조정해야 함",
        "decision": "우선 점검 대상과 조정 사유, 다음 확인일을 결정",
    },
    {
        "id": "cost-schedule",
        "title": "비용·일정 영향 검토서",
        "purpose": "기술 또는 운영 변경이 비용·납기·품질에 미치는 영향을 비교",
        "records": "비용 추정표, 일정안, 가정 목록, 위험 대응표",
        "trigger": "변경안별 자원 투입과 납기 영향이 달라 의사결정 근거가 필요함",
        "decision": "선택안과 보류 조건, 비용 재산정 시점을 결정",
    },
    {
        "id": "audit-prep",
        "title": "내부 점검 증적 정리서",
        "purpose": "점검에서 확인할 변경 이력과 증적 연결 상태를 사전에 점검",
        "records": "증적 목록, 변경 이력, 승인 기록, 미비 항목 조치표",
        "trigger": "내부 점검 또는 외부 검토 전에 증적 누락 여부를 확인해야 함",
        "decision": "보완 필요 항목과 책임자, 종료 기준을 결정",
    },
    {
        "id": "expansion-review",
        "title": "운영 확대 적용 검토서",
        "purpose": "소규모 적용 결과를 바탕으로 대상 범위를 확대할 수 있는지 판단",
        "records": "시범 결과, 대상별 영향표, 교육 계획, 확산 일정",
        "trigger": "시범 운영이 종료되어 다른 조직 또는 현장으로 확대 여부를 결정해야 함",
        "decision": "확대 범위와 선행 조건, 중단 기준을 결정",
    },
)

SHAPES = (
    ("review-report", "검토보고서", "short"),
    ("change-log", "변경관리 기록", "short"),
    ("work-instruction", "작업지침서", "short"),
    ("issue-register", "이슈관리대장", "short"),
    ("validation-report", "검증결과서", "medium"),
    ("meeting-minutes", "검토회의록", "medium"),
    ("risk-assessment", "위험평가서", "medium"),
    ("handoff-note", "업무인계서", "medium"),
    ("operating-plan", "운영계획서", "long"),
    ("root-cause", "원인분석서", "long"),
    ("decision-memo", "의사결정 메모", "long"),
    ("evidence-index", "증적색인서", "long"),
)

LENGTHS = {
    "short": (3100, 5000, 0),
    "medium": (3700, 6200, 2),
    "long": (4100, 8200, 5),
}

EXTENSIONS = (
    "검토자는 입력값의 출처와 산정 기준을 분리해 기록했다. 서로 다른 기간의 값을 비교할 때에는 계절성·설비 상태·작업자 교대처럼 결과를 바꿀 수 있는 조건을 함께 표기하고, 확인되지 않은 값은 확정값으로 쓰지 않았다.",
    "조치 우선순위는 영향 범위, 복구 난이도, 검증 가능성, 다음 작업과의 의존성을 기준으로 정했다. 우선순위가 낮은 항목이라도 안전·품질·계약 조건에 영향을 주면 별도 보류 목록에 남겨 다음 점검에서 다시 확인한다.",
    "회의에서 제기된 이견은 결론과 분리해 기록한다. 반대 의견의 근거가 새 측정값이나 누락 증적과 연결되면 담당자는 기존 결론을 수정할 수 있으며, 수정 이력에는 판단 시점과 변경 사유를 남긴다.",
    "외부 설명이 필요한 경우에는 업무 수행에 필요한 결과와 조건만 전달한다. 원자료, 세부 조합, 실패 이력처럼 판단 근거를 재구성할 수 있는 정보는 전달 목적·수신자·보관 기간이 확인된 경우에만 범위를 정한다.",
    "종료 점검에서는 목표 값 충족뿐 아니라 복구 절차의 실제 작동 여부, 담당 역할의 인수인계, 미결 이슈의 다음 점검일을 확인한다. 하나라도 비어 있으면 종료가 아니라 보완 상태로 분류하고 책임자를 지정한다.",
)

CONTROL_APPENDICES = (
    "자료의 원본 파일명·작성 시각·수집 경로·확인자를 함께 기록하고, 요약표를 새로 만들 때에는 원본과의 연결값을 남긴다. 같은 수치라도 집계 기준이 다르면 별도 열로 표시해 비교 가능한 값과 참고용 값을 구분한다. 이 원칙은 이후 담당자가 문서를 인계받아도 결론의 출발점을 다시 확인할 수 있게 한다.",
    "변경 전 기준은 적용 대상, 적용 시점, 제외 대상, 예외 승인자를 포함해 적는다. 변경 후 기준은 무엇을 대체했는지와 어떤 관찰 결과로 재검토하는지를 연결한다. 이력에는 단순히 수정 사실만 남기지 않고 변경이 품질·비용·납기·안전에 미친 영향을 함께 적어야 한다.",
    "검증 중 관찰된 불일치는 오류로 단정하지 않고 원자료 오류, 측정 조건 차이, 작업 절차 미준수, 외부 환경 변화로 나눠 확인한다. 원인을 확정할 수 없는 경우에는 임시 조치의 유효 기간과 재확인 방법을 정하고, 확정 전 결론이 다른 업무에 자동 전파되지 않도록 범위를 제한한다.",
    "담당자 교체 또는 협력사 작업이 예정된 경우에는 인수인계 시점, 전달 항목, 미결 이슈, 회수해야 할 자료를 체크리스트로 관리한다. 전달받은 사람은 이해 여부를 확인하고, 설명만으로 재현하기 어려운 항목은 원자료 또는 승인된 요약본과 연결해 보관한다.",
    "결정 회의에서는 찬성·반대 의견을 결과와 분리해 남긴다. 반대 의견이 새로운 측정값, 계약 조건, 안전 기준과 연결될 때에는 결론을 재검토할 책임자와 기한을 지정한다. 이렇게 남긴 의견은 다음 변경에서 같은 위험을 반복하지 않기 위한 운영 지식으로 사용한다.",
    "예외 처리 후에는 정상 상태로 되돌아갔다는 사실만으로 종료하지 않는다. 예외가 다시 발생할 가능성, 관련 업무에 남은 영향, 임시 설정의 회수 여부를 점검하고, 필요하면 다음 정기 점검까지 관찰 항목을 유지한다. 종료 판단과 관찰 종료 판단은 서로 다른 기록으로 남긴다.",
    "자료 보관 기간이 끝나거나 전달 목적이 사라진 경우에는 회수·폐기 여부와 보관 책임을 확인한다. 업무상 필요한 요약 정보와 원문 전체를 구분하며, 재사용이 필요한 경우에도 최초 검토 목적을 벗어나지 않는지 다시 판단한다. 이 과정의 기록은 접근 통제 운영 여부를 확인하는 근거가 된다.",
    "최종 확인자는 문서에 적힌 수치, 역할, 일정, 조치가 서로 모순되지 않는지 읽어본다. 문장상 그럴듯해 보이더라도 원자료와 연결되지 않거나 책임자가 없는 조치는 미완료로 표시한다. 다음 검토자가 같은 판단을 재현할 수 있을 정도의 맥락이 남았는지를 최종 품질 기준으로 삼는다.",
)

CONTROL_APPENDIX_COUNT = {"short": 5, "medium": 7, "long": 8}

COMPREHENSIVE_APPENDIX = """검토 결과를 실제 운영에 반영하기 전에, 작성자는 본문에 적힌 수치가 어떤 단위와 기준 기간으로 계산됐는지 다시 확인한다. 목표 범위 안에 있는 값이라도 측정 조건이 바뀌었거나 관찰 대상이 달라졌다면 이전 결과와 직접 비교하지 않는다. 비교가 가능한 경우와 참고만 가능한 경우를 구분해 적는 것이 다음 담당자의 재검토 시간을 줄인다.

검증 증적은 결론을 지지하는 자료만 모으지 않는다. 기대와 다른 결과, 실패한 시도, 보류된 항목도 같은 문서 체계 안에서 연결한다. 다만 실패 이력이 다음 업무에 어떤 영향을 주는지와 공개 또는 전달 시 필요한 범위를 구분해, 불필요한 정보가 넓게 퍼지지 않도록 관리한다.

작업 과정에서 기준이 바뀌면 변경 전 기준, 변경 사유, 승인자, 적용 시점을 함께 남긴다. 구두 협의로만 정리된 사항은 회의록이나 확인 메시지로 전환해 담당자 간 해석 차이를 줄인다. 일정이 촉박하더라도 복구 기준과 중단 기준이 빠지면 적용 결정을 확정하지 않는다.

운영 종료 또는 다음 단계 전환 시에는 자료의 보관 위치와 접근 책임을 확인한다. 협력사·현장 담당자·검토 책임자가 서로 다른 경우에는 누가 어떤 범위의 자료를 보유하는지, 더 이상 필요하지 않은 자료를 어떻게 회수하거나 폐기하는지를 기록한다. 이 기록은 실제 통제 조치가 문서의 설명과 일치하는지 확인하는 근거가 된다.

마지막으로 검토 책임자는 본문을 읽지 않은 제3자가 원자료 위치, 의사결정 이유, 예외 처리, 후속 책임자를 따라갈 수 있는지 점검한다. 연결이 끊긴 항목은 완료가 아니라 보완으로 남기고, 보완 기한과 확인 방법을 지정한다. 이러한 확인을 거쳐야 수치만 나열한 요약이 아니라 재현 가능한 업무 문서가 된다."""

CLOSING_VERIFICATION = """작성·검토·승인 단계가 서로 다른 경우에는 각 단계의 판단을 하나의 결론으로 섞지 않는다. 작성자는 관찰 사실과 제안 조치를 구분하고, 검토자는 근거의 충분성과 예외의 처리 상태를 확인하며, 승인자는 적용 범위와 책임 배분을 확정한다. 세 단계 중 어느 하나가 남아 있으면 문서는 완료가 아닌 진행 중 상태로 관리한다. 다음 검토에서는 이 상태값과 미결 항목이 실제 조치로 연결됐는지 먼저 확인한다."""

VARIANT_FOCI = (
    "이번 차수는 원자료와 요약표의 수치가 같은 기준으로 계산됐는지 확인하는 데 초점을 둔다.",
    "이번 차수는 예외 발생 시 담당자 간 전달 순서와 복구 기준이 실제로 연결되는지 확인하는 데 초점을 둔다.",
    "이번 차수는 변경 전후 결과가 다른 경우 그 차이를 설명할 증적이 남아 있는지 확인하는 데 초점을 둔다.",
    "이번 차수는 외부 전달 필요성과 내부 보관 필요성을 구분해 접근 기록이 누락되지 않았는지 확인하는 데 초점을 둔다.",
)


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _family_case(base: dict[str, str], package: dict[str, str]) -> dict[str, str]:
    return {
        **base,
        "case_id": f"{base['case_id']}-{package['id']}",
        "document_type": f"{base['document_type']} {package['title']}",
        "context": f"{base['context']}에서 {package['purpose']}하는 업무",
        "detail": f"{base['detail']}을 바탕으로 {package['records']}를 서로 대조하는 기준",
        "metric": base["metric"],
        "public": base["public"],
        "roles": base["roles"],
        "package_purpose": package["purpose"],
        "package_records": package["records"],
        "package_trigger": package["trigger"],
        "package_decision": package["decision"],
    }


def _shape_indices_for_grade(grade: str, target: int) -> list[int]:
    """Mirror the pool's seeded shape quotas for every authored document."""
    shapes = [f"direct-{shape_id}" for shape_id, _name, _length in SHAPES]
    shape_to_length = {
        f"direct-{shape_id}": f"direct-{length}"
        for shape_id, _name, length in SHAPES
    }
    lengths = sorted(set(shape_to_length.values()))
    length_quotas = _balanced_quotas(
        lengths, target, seed=f"{SELECTION_SEED}:{grade}:length"
    )
    shape_quotas: dict[str, int] = {}
    for length in lengths:
        grouped_shapes = sorted(
            shape for shape in shapes if shape_to_length[shape] == length
        )
        shape_quotas.update(
            _balanced_quotas(
                grouped_shapes,
                length_quotas[length],
                seed=f"{SELECTION_SEED}:{grade}:shape:{length}",
            )
        )
    sequence = [
        shapes.index(shape)
        for shape, count in sorted(shape_quotas.items())
        for _ in range(count)
    ]
    assert len(sequence) == target
    return sequence


def _body(
    case: dict[str, str],
    *,
    grade: str,
    variant: int,
    shape: tuple[str, str, str],
) -> str:
    policy = GRADE_POLICY[grade]
    shape_id, shape_name, length_name = shape
    _minimum, _maximum, extension_count = LENGTHS[length_name]
    disclosure = str(policy["disclosure"])
    if grade == "S3":
        disclosure += f"{case['public']}에 근거한다."
    extensions = "\n\n".join(EXTENSIONS[: extension_count + variant % 2])
    sections = (
        f"""# {case['document_type']}

문서 형식: {shape_name} / 작성 목적: {case['package_purpose']}

## 검토 배경

본 문서는 {case['context']}에 관한 판단 기록이다. 이번 검토가 시작된 계기는 {case['package_trigger']}라는 점이며, 검토 범위는 변경 전후 조건, 예외 처리, 검증 증적, 책임 분담이다. 작성자는 일반적인 업계 관행과 현재 업무에서 확인된 사실을 구분하고, 확인되지 않은 추정은 결론 근거에서 제외한다.

## 대상과 관찰 사실

핵심 검토 대상은 {case['detail']}이다. 동일 기준으로 대조한 결과 {case['metric']} 수치 하나만으로 결론을 고정하지 않고, 입력 조건·측정 시점·예외 발생 여부를 함께 기록했다. 이번 문서에 연결한 자료는 {case['package_records']}이며, 각 자료의 작성자와 변경 시점을 대조 가능하게 남긴다.

## 실행 및 확인 절차

첫째, 원자료의 작성 시점과 변경 이력을 확인한다. 둘째, 조건별 결과를 비교해 누락 구간은 보완 확인 대상으로 분리한다. 셋째, 적용 전에는 예외 처리와 복구 기준을 책임자가 확인한다. 넷째, 적용 후에는 두 번 이상의 관찰 주기에서 같은 지표를 추적하고 기준 이탈 시 이전 절차로 되돌린다. 역할은 {case['roles']}가 나누어 수행한다.

## 공개성·가치·관리 상태

{disclosure}

{policy['access']}

{policy['value']}

## 결정 및 후속 조치

이번 검토의 결정 항목은 {case['package_decision']}이다. 담당자는 적용 결과, 변경 이력, 접근 기록, 미결 이슈를 연결해 보관한다. 예외가 발견되면 영향 범위·임시 조치·복구 시점·재검토 책임자를 별도 기록하고, 다음 회의에서 완료 여부를 확인한다.

## 종료 기준

종료 기준은 지표 충족만이 아니라 예외 처리 완료, 증적 연결 확인, 역할별 인수인계, 다음 점검일 지정까지 포함한다. 기준을 만족하지 못하면 적용을 보류하고 보류 사유를 다음 검토의 입력으로 넘긴다.
""",
        f"""# {case['document_type']}

문서 형식: {shape_name} / 검토 기준: 변경 이력과 현장 증적의 일치

## 요청 사항

{case['package_purpose']}를 위해 현재 조건과 변경안을 비교한다. 요청 발생 사유는 {case['package_trigger']}이며, 본 문서는 결론만 제시하지 않고 확인된 사실·미확인 항목·후속 책임을 구분한다.

## 현황 요약

{case['context']}과 관련해 {case['detail']}을 검토했다. 관찰 결과 {case['metric']} 담당자는 자료 {case['package_records']}를 대조해 누락 또는 상충되는 값을 표시하고, 값의 출처가 다른 경우에는 동일한 의미로 합산하지 않는다.

## 검증 체크리스트

1. 변경 전 기준과 적용 대상이 명확한가.
2. 예외 발생 시 복구 순서와 승인 책임자가 정해졌는가.
3. 측정 결과가 작성 시점·조건·담당자와 연결되는가.
4. 외부 전달 자료와 내부 판단 근거가 분리됐는가.

## 판단 근거

{disclosure}

{policy['value']}

{policy['access']}

## 조치 기록

{case['package_decision']}을 결정하기 전, 담당자는 비교표와 확인 이력을 갱신한다. 보완이 필요한 항목은 완료 예정일과 책임자를 지정하고, 작업 완료 후 같은 조건에서 다시 확인한다. 결론이 바뀌면 변경 사유와 영향 범위를 별도 이력으로 남긴다.
""",
    )
    body = sections[variant % len(sections)]
    body += (
        "\n\n## 차수별 보완 초점\n\n"
        + VARIANT_FOCI[variant % len(VARIANT_FOCI)]
    )
    if extensions:
        body += "\n\n## 추가 검토 메모\n\n" + extensions
    control_appendix = "\n\n".join(
        CONTROL_APPENDICES[: CONTROL_APPENDIX_COUNT[length_name]]
    )
    body += "\n\n## 기록 관리와 재현성 확인\n\n" + control_appendix
    body += "\n\n## 종합 확인 의견\n\n" + COMPREHENSIVE_APPENDIX
    body += "\n\n## 작성·검토·승인 상태\n\n" + CLOSING_VERIFICATION
    return body.strip() + "\n"


def _record(
    case: dict[str, str],
    *,
    grade: str,
    variant: int,
    family_index: int,
    shape_index: int,
) -> dict[str, object]:
    shape = SHAPES[shape_index]
    shape_id, _shape_name, length_name = shape
    minimum, maximum, _extension_count = LENGTHS[length_name]
    policy = GRADE_POLICY[grade]
    text = _body(case, grade=grade, variant=variant, shape=shape)
    family_id = f"direct-train-v2-{case['case_id']}"
    return {
        "doc_id": f"{family_id}-{grade.lower()}-{variant + 1}",
        "document_family_id": family_id,
        "scenario_id": f"direct-v2-{case['case_id']}-{grade.lower()}",
        "factor_profile_id": f"direct-{grade.lower()}-svm",
        "family_profile_id": f"direct-{shape_id}",
        "length_profile_id": f"direct-{length_name}",
        "requested_profile_min_chars": minimum,
        "requested_profile_max_chars": maximum,
        "document_type": case["document_type"],
        "domain": case["case_id"].split("-", 1)[0],
        "industry": "direct-authored-proxy",
        "text": text,
        "label": grade,
        "intended_label": grade,
        "expected_factor_scores": dict(policy["scores"]),
        "evidence_card": _evidence_card(text, policy, case),
        "document_origin": "synthetic",
        "source": "direct_authored_proxy",
        "proxy_role": "confidential_simulation",
        "catalog_split_role": "train_pool_only",
        "training_use_permitted": True,
        "evaluation_use_permitted": False,
        "authoring_method": "codex_direct_authored_training_corpus_v6",
        "generation_lineage": ["generator:codex:direct-authored-training-v6"],
        "decision_bucket": "direct_authored_training_candidate",
        "gate_version": "direct_authored_quality_v1",
        "primary_judge_model": "codex-editorial-audit-v1",
        "judging_lineage": ["primary_judge:codex-editorial-audit-v1"],
        "consensus_evidence": _editorial_audit(policy, grade),
        "requires_manual_audit": False,
        "claim_scope": (
            "Direct-authored Proxy training only; not customer-real evidence, "
            "golden evaluation, or Locked Gold."
        ),
    }


def build_records() -> list[dict[str, object]]:
    families = [
        _family_case(case, package) for case in CASES for package in WORK_PACKAGES
    ]
    assert len(families) == 225
    specifications: dict[str, list[tuple[int, dict[str, str], int]]] = {
        grade: [] for grade in ("TS", "S1", "S2", "S3")
    }
    for family_index, case in enumerate(families):
        for grade, base_count in GRADE_VARIANTS.items():
            count = base_count + (
                1 if grade in {"TS", "S1", "S2"} and family_index < 75 else 0
            )
            specifications[grade].extend(
                (family_index, case, variant) for variant in range(count)
            )
    rows: list[dict[str, object]] = []
    for grade, specs in specifications.items():
        shape_indices = _shape_indices_for_grade(grade, len(specs))
        rows.extend(
            _record(
                case,
                grade=grade,
                variant=variant,
                family_index=family_index,
                shape_index=shape_index,
            )
            for (family_index, case, variant), shape_index in zip(
                specs, shape_indices, strict=True
            )
        )
    assert Counter(str(row["label"]) for row in rows) == {
        "TS": 750,
        "S1": 750,
        "S2": 750,
        "S3": 450,
    }
    assert len(specifications["TS"]) - 225 * 3 == EXTRA_HIGH_GRADE_FAMILIES
    return rows


def _write_new(path: Path, payload: bytes) -> None:
    if path.exists():
        raise RuntimeError(f"refusing to overwrite immutable output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{uuid.uuid4().hex}")
    try:
        with temporary.open("xb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    rows = build_records()
    failures = {
        str(row["doc_id"]): list(
            validate_proxy_record(row, stage="eligible", intended_use="training").errors
        )
        for row in rows
        if not validate_proxy_record(row, stage="eligible", intended_use="training").ok
    }
    if failures:
        raise RuntimeError(json.dumps(failures, ensure_ascii=False, indent=2))
    payload = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for row in rows
    )
    _write_new(OUT, payload)
    manifest = {
        "schema": "direct-authored-proxy-training-corpus-v6",
        "records": len(rows),
        "families": len({str(row["document_family_id"]) for row in rows}),
        "grade_counts": dict(sorted(Counter(str(row["label"]) for row in rows).items())),
        "document_shapes": len({str(row["family_profile_id"]) for row in rows}),
        "length_profiles": len({str(row["length_profile_id"]) for row in rows}),
        "records_sha256": _sha256(payload),
        "training_only": True,
        "authoring_method": "codex_direct_authored_training_corpus_v6",
        "claim_scope": "Proxy training only; not customer-real accuracy evidence or Locked Gold.",
    }
    _write_new(
        MANIFEST,
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode(
            "utf-8"
        ),
    )
    print(json.dumps({"records": len(rows), "output": str(OUT), **manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
