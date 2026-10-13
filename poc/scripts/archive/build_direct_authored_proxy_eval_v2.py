"""Build a new, independent frozen Proxy evaluation source.

This is deliberately separate from the v1 evaluation families and from every
training corpus.  It uses no model or LLM call: the case ledger, evidence
patterns, and grade criteria below are directly authored.  Once materialized,
the output is immutable and must not be used for training or calibration.
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

from koipa.proxy_corpus import validate_proxy_record
from scripts.build_direct_authored_training_pilot import (
    GRADE_POLICY,
    _editorial_audit,
    _evidence_card,
)


OUT = ROOT / "datasets" / "proxy_eval" / "direct_authored_proxy_eval.v2_1.jsonl"
MANIFEST = ROOT / "datasets" / "proxy_eval" / "direct_authored_proxy_eval.v2_1.manifest.json"

# These domains intentionally do not reuse the case ledger used by the v1
# evaluation or the catalog-bound training v1 corpus.
CASES = (
    ("rail-signal", "철도 신호 전환 장애 분석 기록", "신호 전환 지연", "전환 순서·복구 조건·현장 계측값"),
    ("port-berth", "항만 선석 배정 검토서", "하역 순서 변경", "선석 우선순위·도착 예측·대체 배정 조건"),
    ("wind-turbine", "풍력 설비 진동 진단 보고", "기어박스 진동 증가", "측정 구간·보정 계수·교체 판단 기준"),
    ("insulin-fill", "의약품 충전 공정 편차 검토", "충전량 편차", "충전 압력·노즐 보정값·배치별 예외 조건"),
    ("warehouse-slot", "물류센터 적치 위치 변경안", "피킹 동선 과부하", "수요 예측·적치 규칙·긴급 주문 우선순위"),
    ("lithography-mask", "노광 마스크 결함 대응 회의록", "패턴 결함 증가", "결함 좌표·재작업 순서·허용 기준"),
    ("smart-meter", "전력 계량 이상 탐지 기준서", "계량값 편차", "탐지 임계값·가입자 군집·검증 순서"),
    ("drone-flight", "무인기 비행시험 변경 검토", "측풍 조건 변경", "비행 경로·제어 보정값·중단 기준"),
    ("ship-ballast", "선박 평형수 처리 운전일지", "처리 효율 저하", "약품 투입량·유량 조합·재측정 조건"),
    ("battery-recall", "배터리 회수 판정 메모", "열화 징후 확인", "셀 이력·판정 순서·격리 기준"),
    ("credit-limit", "거래 한도 예외 승인 검토", "신규 거래처 한도", "위험 지표 조합·승인 경로·회수 조건"),
    ("fiber-routing", "광망 우회 경로 점검 기록", "구간 손실 증가", "우회 순서·광세기 조건·절체 시간"),
    ("robot-gripper", "협동로봇 그리퍼 교체 검토서", "파지 실패 증가", "압력 곡선·부품 조합·검증 절차"),
    ("airport-gate", "공항 탑승구 운영 변경안", "연결편 지연", "배정 우선순위·환승 시간·대체 게이트 규칙"),
    ("water-leak", "상수관 누수 탐지 결과서", "야간 유량 이상", "구간별 기준값·청음 결과·굴착 우선순위"),
    ("claims-triage", "보험 청구 심사 우선순위표", "복합 청구 증가", "증빙 조합·추가 확인 규칙·보류 기준"),
    ("biobank-freeze", "바이오 시료 동결 보관 점검서", "온도 이탈", "이탈 시간·시료 등급·재검 기준"),
    ("factory-air", "공장 배기 설비 개선 검토", "배출 농도 변동", "운전 조합·필터 교체 시점·경보 기준"),
    ("taxi-demand", "도시 이동 수요 배차 검토", "행사 시간대 쏠림", "수요 예측값·공차 위치·우선 배차 규칙"),
    ("elevator-brake", "승강기 제동 시험 결과서", "제동 거리 편차", "하중 조건·보정 절차·합격 한계"),
    ("crop-storage", "저온 저장고 품질 유지 기록", "숙도 편차", "온습도 조합·출고 순서·격리 기준"),
    ("payment-fraud", "결제 이상 패턴 검토서", "해외 결제 급증", "거래 조합·보류 순서·해제 조건"),
    ("dam-gate", "댐 수문 제어 변경 기록", "유입량 급변", "개도 순서·수위 예측·비상 전환 조건"),
    ("lab-reagent", "시험실 시약 교체 영향 분석", "반응값 이동", "농도 보정·장비 조건·재현성 기준"),
    ("recycling-bale", "재활용 압축품 등급 판정서", "이물 혼입", "선별 조건·압축 순서·반송 기준"),
)

FORMS = (
    "현장 확인서", "변경 영향 메모", "검증 계획서", "예외 처리 기록", "운영 검토 보고", 
    "품질 이슈 회의록", "시험 결과서", "승인 요청서", "조치 완료 확인서", "재발 방지 검토서",
)

GRADE_EVIDENCE = {
    "S3": {
        "secrecy": "문서의 기준과 수치는 공개 지침·공개 규격·이미 배포된 안내자료에서 그대로 확인된다.",
        "value": "일반 운영에 필요한 확인 절차만 담고 있어 별도 경쟁상 이점이나 비공개 사업상 가치는 확인되지 않는다.",
        "management": "공개 자료로 관리되며 접근 제한·반출 통제·수신자 제한을 적용하지 않았다.",
    },
    "S2": {
        "secrecy": "일부 내부 작업 순서는 포함하지만 핵심 수치와 결합 규칙은 일반 공개 자료 또는 통상적 업무지식으로 재현 가능하다.",
        "value": "운영상 참고 가치는 있으나 특정 고객·설비·사업 기회의 우위를 직접 만들 정도의 독자성은 확인되지 않는다.",
        "management": "프로젝트 구성원에게 공유되었으나 전용 저장소·반출 승인·수신자 기록 중 일부가 빠져 관리조치가 제한적이다.",
    },
    "S1": {
        "secrecy": "공개된 일반 원칙과 달리, 현장 조건·예외 순서·수치 조합이 함께 있어 외부인이 동일 결과를 재현하기 어렵다.",
        "value": "재작업 감소, 납기 안정화 또는 계약상 손실 회피와 연결되는 구체적 운영상 가치가 확인된다.",
        "management": "업무 필요 인원으로 접근을 제한하고 수신자 목록·저장 위치·반출 금지 표기를 운영하지만 전사 통제까지는 아니다.",
    },
    "TS": {
        "secrecy": "미공개 설계·제어 조건·실패 이력의 결합으로 구성돼 공개 자료만으로는 핵심 조합을 재현할 수 없다.",
        "value": "핵심 공정의 수율·원가·고객 대응 또는 시장 진입 시점에 직접 영향을 주는 고가치 정보임이 기록으로 확인된다.",
        "management": "개별 권한·다운로드 기록·반출 승인·정기 권한 회수와 같은 통제를 적용하고, 열람 및 전달 이력이 추적된다.",
    },
}

COMMON_APPENDIX = """
## 기록의 해석 범위

이 문서는 현장의 관찰값과 그에 따른 조치 기준을 구분해 남긴다. 수치 하나만으로 등급을 정하지 않고, 공개 여부·결합된 사업상 가치·접근통제의 실제 작동 여부를 함께 확인한다. 검토자는 추정이나 사후 설명을 사실처럼 적지 않으며, 확인되지 않은 항목은 보류로 남긴다.

## 후속 조치

담당자는 변경 전후의 조건, 적용 대상, 예외 발생 여부, 복구 절차를 별도 이력에 기록한다. 같은 문장을 다른 업무로 옮겨 적는 경우에는 적용 범위가 달라지지 않았는지 다시 확인하고, 수신자 범위를 넘는 전달은 승인 절차를 따른다.
""".strip()

NUMERIC_AUDIT_APPENDIX = """

## 수치 확인 기록

이번 차수에는 3개 관찰 지점의 값을 24시간 간격으로 2회 대조했다. 기준값을 벗어난 항목은 1건씩 분리해 재확인하고, 적용 전후의 차이는 동일 측정 방법으로 남긴다. 숫자는 판단의 보조 근거이며, 공개성·가치·관리 근거를 대신하지 않는다.
""".strip()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _body(case: tuple[str, str, str, str], grade: str, ordinal: int) -> str:
    case_id, title, issue, evidence = case
    grade_evidence = GRADE_EVIDENCE[grade]
    policy = GRADE_POLICY[grade]
    form = FORMS[ordinal % len(FORMS)]
    return f"""# {title}

문서 형식: {form} / 관리번호: E2-{case_id.upper()}-{ordinal + 1:03d}

## 변경 안건과 검토 범위

이번 검토의 대상은 {issue}에 대한 대응 기준이다. 담당 부서는 {evidence}를 한 묶음으로 확인하고, 일반 공개 정보와 해당 업무에서만 확인된 사실을 분리해 기록했다. 단일 수치가 목표 범위에 있다고 해서 즉시 적용하지 않으며, 전제 조건·예외·영향 범위를 함께 검토한다.

## 적용 및 검증 결과

현장 기록에는 관찰 시점, 적용 대상, 비교 기준, 재확인 결과를 순서대로 남긴다. 검증은 동일 조건을 반복하는 방식으로 진행하며, 다른 조건에서 나온 결과를 같은 근거로 섞지 않는다. 오류 또는 이탈이 발견되면 적용을 중지하고 원인·임시 조치·재검 일정·책임자를 분리해 기록한다.

## 공개성·가치·관리 근거

공개성: {grade_evidence['secrecy']}

사업상 가치: {grade_evidence['value']}

접근 및 관리: {grade_evidence['management']}

판정 기준 원문: {policy['disclosure']}{'공개 규격과 공개 안내자료에 근거한다.' if grade == 'S3' else ''}

관리 기준 원문: {policy['access']}

가치 기준 원문: {policy['value']}

## 검토 의견 및 결정

결정자는 관찰 사실과 판단 근거를 구분하여 적고, 근거가 부족한 경우에는 완료가 아니라 보류로 처리한다. 적용 후에는 기준이 바뀐 이유와 영향받는 업무 범위를 연결해 남기며, 이전 기록과 상충하는 내용은 수정 이력 없이 덮어쓰지 않는다.

{COMMON_APPENDIX}

{NUMERIC_AUDIT_APPENDIX}
""".strip() + "\n"


def _record(case: tuple[str, str, str, str], grade: str, ordinal: int) -> dict[str, object]:
    case_id, title, _issue, _evidence = case
    text = _body(case, grade, ordinal)
    policy = GRADE_POLICY[grade]
    evidence_case = {
        "public": "공개 규격과 공개 안내자료",
        "document_type": title,
        "context": case_id,
        "detail": title,
        "metric": "3개 관찰 지점의 값을 24시간 간격으로 2회 대조했다.",
        "roles": "검토 책임자와 현장 담당자",
    }
    audit = _editorial_audit(policy, grade)
    audit["gate_status"] = "direct_authored_evaluation_candidate"
    return {
        "doc_id": f"eval-v2-{case_id}-{grade.lower()}-{ordinal:03d}",
        "document_family_id": f"eval-v2-{case_id}-family-{ordinal % 10:02d}",
        "scenario_id": f"eval-v2-{case_id}-{grade.lower()}",
        "factor_profile_id": f"eval-v2-{grade.lower()}-svm",
        "family_profile_id": f"eval-v2-{FORMS[ordinal % len(FORMS)]}",
        "length_profile_id": "direct-eval-v2-3000-4200",
        "requested_profile_min_chars": 3000,
        "requested_profile_max_chars": 4200,
        "document_type": title,
        "domain": "independent-direct-authored-evaluation-v2",
        "industry": "direct-authored-proxy",
        "text": text,
        "label": grade,
        "intended_label": grade,
        "expected_factor_scores": dict(policy["scores"]),
        "evidence_card": _evidence_card(text, policy, evidence_case),
        "document_origin": "synthetic",
        "source": "direct_authored_proxy",
        "proxy_role": "confidential_simulation",
        "catalog_split_role": "frozen_proxy_eval_only",
        "training_use_permitted": False,
        "evaluation_use_permitted": True,
        "authoring_method": "codex_direct_authored_proxy_evaluation_v2",
        "generation_lineage": ["generator:codex:direct-authored-proxy-eval-v2"],
        "decision_bucket": "direct_authored_evaluation_candidate",
        "gate_version": "direct_authored_quality_v1",
        "primary_judge_model": "codex-editorial-audit-v2",
        "judging_lineage": ["primary_judge:codex-editorial-audit-v2"],
        "consensus_evidence": audit,
        "requires_manual_audit": False,
        "claim_scope": "Direct-authored Proxy evaluation only; training forbidden; not customer-real accuracy evidence or Locked Gold.",
    }


def build_records() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    family_ordinal = 0
    for case in CASES:
        for variant in range(10):
            for grade in ("S1", "S2", "S3"):
                rows.append(_record(case, grade, variant))
            if family_ordinal < 200:
                rows.append(_record(case, "TS", variant))
            else:
                # Keep the extra S3 record in the same family so the
                # development/final split remains family-atomic.
                rows.append(_record(case, "S3", variant + 1000))
            family_ordinal += 1
    counts = Counter(str(row["label"]) for row in rows)
    assert len(rows) == 1000 and counts == {"TS": 200, "S1": 250, "S2": 250, "S3": 300}
    assert len({str(row["doc_id"]) for row in rows}) == len(rows)
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
    failures = [row["doc_id"] for row in rows if not validate_proxy_record(row, stage="eligible", intended_use="evaluation").ok]
    if failures:
        raise RuntimeError(f"invalid records: {failures[:20]}")
    payload = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8") for row in rows)
    _write_new(OUT, payload)
    manifest = {
        "schema": "direct-authored-proxy-evaluation-v2",
        "records": len(rows),
        "records_sha256": _sha256(payload),
        "grade_counts": dict(sorted(Counter(str(row["label"]) for row in rows).items())),
        "case_families": len(CASES) * 10,
        "training_forbidden": True,
        "no_llm_generation": True,
        "claim_scope": "Proxy-only future evaluation; not customer-real accuracy or Locked Gold.",
    }
    _write_new(MANIFEST, (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({"output": str(OUT), **manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
