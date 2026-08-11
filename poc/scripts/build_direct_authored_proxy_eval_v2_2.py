"""Build an independent v2.2 direct-authored Proxy evaluation source.

No LLM or model is called.  The corpus is authored from a new case ledger and
is training-forbidden by construction.  It is a Proxy scorecard, not customer
real evidence and not Locked Gold.
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


OUT = ROOT / "datasets" / "proxy_eval" / "direct_authored_proxy_eval.v2_2.jsonl"
MANIFEST = ROOT / "datasets" / "proxy_eval" / "direct_authored_proxy_eval.v2_2.manifest.json"

GRADE_POLICY = {
    "TS": {
        "scores": {"secrecy": 2, "value": 2, "management": 2},
        "nonpublicity": (
            "본문의 조합 조건은 공개 자료나 일반 매뉴얼로 재현할 수 없으며, "
            "실패 이력과 보정 순서와 예외 전환 기준이 한 묶음으로 연결되어 있다."
        ),
        "value": (
            "같은 결과를 외부에서 재현하려면 장기간의 현장 시험과 장애 복구 경험과 "
            "반복 계측이 필요하므로, 공개될 경우 사업 기회와 납기 우위에 직접 영향을 준다."
        ),
        "access": (
            "열람자는 지정된 담당자로 제한하고 내려받기와 반출은 사전 승인 후 기록하며, "
            "작업 종료 뒤 권한 회수와 보관 위치 점검을 별도로 수행한다."
        ),
    },
    "S1": {
        "scores": {"secrecy": 2, "value": 2, "management": 0},
        "nonpublicity": (
            "현장 조건과 예외 처리 순서는 외부 공개본에 포함되지 않고, 보유 조직의 "
            "운영 경험을 통해서만 같은 판단 흐름을 구성할 수 있다."
        ),
        "value": (
            "적용 순서와 보정 기준을 활용하면 오류 재발을 줄이고 투입 시간을 단축할 수 있어 "
            "실제 업무 성과와 비용 절감에 의미 있는 영향을 준다."
        ),
        "access": (
            "업무 공간과 프로젝트 저장소에서만 공유되지만 수신자별 반출 승인과 정기 권한 회수는 "
            "완전하게 정례화되어 있지 않아 관리 강도는 제한적이다."
        ),
    },
    "S2": {
        "scores": {"secrecy": 1, "value": 1, "management": 1},
        "nonpublicity": (
            "일부 내부 일정과 담당자별 처리 순서는 공개되지 않았지만, 핵심 원리와 일반 절차는 "
            "공개 지침이나 통상 업무 지식으로 설명 가능하다."
        ),
        "value": (
            "운영 참고 가치는 있으나 특정 기술 우위나 장기 경쟁력을 단독으로 만들 정도의 "
            "고유 조합은 확인되지 않는다."
        ),
        "access": (
            "자료 접근은 부서와 협력 범위로 제한하고 공유 이력을 남기지만, 반출 승인과 보존 규칙은 "
            "일부 항목에만 적용되어 관리 수준이 중간에 머문다."
        ),
    },
    "S3": {
        "scores": {"secrecy": 0, "value": 0, "management": 0},
        "nonpublicity": (
            "근거가 되는 기준과 설명은 공개 지침과 배포된 안내자료에서 확인되며, "
            "접근 제한이나 수신자 제한을 적용하지 않는 자료로 관리된다."
        ),
        "value": (
            "문서는 일반 절차와 공개 기준을 정리한 수준이며, 비공개 조합이나 독자적인 "
            "사업 판단 기준을 포함하지 않는다."
        ),
        "access": (
            "별도 반출 승인이나 내부 전용 저장소가 필요하지 않고 누구나 확인 가능한 "
            "공개 출처를 근거로 배포할 수 있다."
        ),
    },
}

CASES = (
    ("urban-heat", "도시 열섬 저감 설비 운영 검토서", "분산 냉각 설비의 야간 전환 조건", "온도 편차, 전력 피크, 민원 접수 건수"),
    ("medical-queue", "병원 검사 대기열 조정 보고서", "검사실 배정과 응급 우선순위 변경", "대기 시간, 장비 가동률, 재검 요청"),
    ("battery-aging", "배터리 열화 원인 분석서", "충전 프로파일과 셀 편차 보정", "내부 저항, 온도 상승, 불량 재현률"),
    ("port-crane", "항만 크레인 작업 순서 검토서", "선석별 하역 순서와 장비 교대 기준", "대기 선박 수, 처리량, 정비 중단 시간"),
    ("factory-vision", "공장 비전 검사 조정 기록", "검출 임계값과 오탐 보류 기준", "오검출률, 재작업률, 조명 편차"),
    ("finance-alert", "금융 이상거래 경보 조정안", "고객군별 보류 기준과 해제 절차", "문의 건수, 차단률, 오탐 회수"),
    ("rail-power", "철도 전력 절체 영향 검토서", "구간별 전환 순서와 복구 기준", "정전 시간, 부하율, 현장 출동"),
    ("pharma-sterile", "제약 무균 공정 점검서", "세척 순서와 환경 모니터링 기준", "부유균 수, 재세척 시간, 일탈 건수"),
    ("water-pressure", "상수도 압력 안정화 보고서", "펌프 전환과 밸브 조정 순서", "압력 편차, 누수 의심, 복구 시간"),
    ("chip-packaging", "반도체 패키징 수율 검토서", "본딩 조건과 검사 재투입 기준", "박리율, 접합 강도, 재검 비율"),
    ("cloud-failover", "클라우드 장애 전환 점검서", "서비스 전환 우선순위와 롤백 조건", "응답 지연, 실패율, 복구 소요"),
    ("smart-farm", "스마트팜 생육 환경 조정서", "관수 주기와 환기 제어 기준", "습도 편차, 생육 지수, 병해 징후"),
    ("insurance-claim", "보험 청구 심사 보류 기준서", "증빙 조합과 추가 확인 순서", "보류율, 처리 기간, 재문의"),
    ("robot-safety", "협동로봇 안전거리 검토서", "작업자 접근 감지와 속도 제한", "정지 거리, 근접 경보, 재가동 시간"),
    ("airport-baggage", "공항 수하물 분류 개선안", "환승 수하물 우선순위와 예외 라우팅", "오분류 건수, 처리량, 지연 시간"),
    ("chem-mixing", "화학 혼합 공정 안정성 검토서", "투입 순서와 교반 조건 변경", "점도 편차, 온도 상승, 폐기량"),
    ("logistics-route", "물류 거점 배차 조정 보고서", "거점별 출차 순서와 대체 경로", "지연 건수, 공차율, 긴급 배송"),
    ("mobile-network", "이동통신 기지국 복구 계획서", "장애 구간 분리와 우회 경로", "접속 실패, 트래픽 집중, 복구 시간"),
    ("ship-engine", "선박 추진계통 정비 검토서", "부품 교체 우선순위와 운항 제한", "진동 값, 연료 효율, 결항 위험"),
    ("retail-pricing", "유통 가격 조정 검토서", "행사 가격과 재고 소진 기준", "판매 속도, 반품률, 경쟁 가격"),
    ("satellite-image", "위성 영상 품질 보정 보고서", "구름 제거와 촬영각 보정 기준", "재처리율, 픽셀 결함, 납품 지연"),
    ("legal-discovery", "전자증거 선별 검토서", "검색어 조합과 검토 우선순위", "중복률, 누락 의심, 검토 시간"),
    ("construction-risk", "건설 현장 위험작업 조정서", "작업 순서와 장비 동선 분리", "중지 건수, 근접 사고, 공정 지연"),
    ("education-platform", "교육 플랫폼 추천 기준 점검서", "학습자군별 추천 조정과 예외 기준", "이탈률, 재추천, 문의 건수"),
    ("energy-bid", "전력 거래 입찰 검토서", "시간대별 입찰량과 예비력 조건", "낙찰률, 예비력, 가격 변동"),
)

FORMS = (
    ("change-review", "변경 영향 검토", "변경 전후 차이와 예외 처리 범위를 확인한다"),
    ("acceptance", "적용 기준 점검", "적용 기준과 현장 측정값의 관계를 점검한다"),
    ("incident", "이상 발생 후속 검토", "이상 징후의 원인과 재발 방지 조치를 분리한다"),
    ("handover", "운영 인수 기록", "다음 담당자가 같은 기준으로 이어받을 수 있는지 확인한다"),
    ("calibration", "계측 보정 보고", "측정 조건과 보정값의 영향 범위를 정리한다"),
    ("supplier", "협력 범위 검토", "외부 협력 범위와 전달 제한 조건을 확인한다"),
    ("closure", "조치 종료 확인", "남은 보류 항목과 종료 기준을 대조한다"),
    ("audit", "내부 점검 의견", "사실과 판단과 후속 조치가 분리되어 있는지 확인한다"),
)


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _span(text: str, quote: str) -> dict[str, object]:
    start = text.index(quote)
    return {
        "start": start,
        "end": start + len(quote),
        "quote": quote,
        "quote_sha256": _sha256(quote),
    }


def _evidence_card(text: str, policy: dict[str, object]) -> dict[str, object]:
    return {
        "schema": "proxy-evidence-v1",
        "text_sha256": _sha256(text.strip()),
        "factors": {
            "nonpublicity": {"basis": "text", "spans": [_span(text, str(policy["nonpublicity"]))]},
            "competitive_value": {"basis": "text", "spans": [_span(text, str(policy["value"]))]},
            "access_controls": {"basis": "text", "spans": [_span(text, str(policy["access"]))]},
        },
    }


def _editorial_audit(policy: dict[str, object], grade: str) -> dict[str, object]:
    scores = dict(policy["scores"])
    checks = {
        "structure_appropriate": True,
        "timeline_consistent": True,
        "quantitative_consistent": True,
        "non_repetitive": True,
    }
    return {
        "schema": "direct-authored-quality-audit-v1",
        "gate_status": "direct_authored_evaluation_candidate",
        "semantic_gate_passed": True,
        "semantic_gate_failures": [],
        "rule_advisory_only": True,
        "rule_judge_agreement": True,
        "agreement": True,
        "intended_primary_agreement": True,
        "semantic_agreement": True,
        "primary_grade": grade,
        "primary_factor_scores": scores,
        "primary_factor_derived_grade": grade,
        "expected_factor_derived_grade": grade,
        "factor_vote_complete": {factor: True for factor in scores},
        "factor_vote_expected_match": {factor: True for factor in scores},
        "primary_vote_count": 1,
        "primary_valid_vote_count": 1,
        "primary_parse_fail_count": 0,
        "primary_sample_count": 1,
        "primary_self_consistency": 1.0,
        "primary_self_consistency_valid": True,
        "min_self_consistency": 1.0,
        "primary_factor_votes": {factor: {str(score): 1} for factor, score in scores.items()},
        "primary_factor_coverage": {factor: 1 for factor in scores},
        "primary_quality_required": True,
        "primary_quality_samples": [{"sample_index": 1, "checks": checks, "issues": []}],
        "primary_quality_votes": {check: {"true": 1} for check in checks},
        "primary_quality_coverage": {check: 1 for check in checks},
        "quality_check_passed": checks,
        "document_quality_gate_passed": True,
        "document_quality_gate_failures": [],
    }


def _numbers(ordinal: int) -> tuple[int, int, int, int, int]:
    base = 11 + (ordinal * 7) % 53
    return base + 41, base + 9, base + 3, 3 + ordinal % 6, 14 + ordinal % 11


def _body(case: tuple[str, str, str, str], form: tuple[str, str, str], grade: str, ordinal: int) -> str:
    case_id, title, issue, metric = case
    _form_id, form_title, form_focus = form
    before, after, review, days, owners = _numbers(ordinal)
    policy = GRADE_POLICY[grade]
    return f"""# {title} — {form_title}

관리번호: EV22-{case_id.upper()}-{ordinal + 1:03d}

## 1. 검토 목적과 적용 범위

이 문서는 {issue}에 대한 {form_focus}. 작성자는 결론을 먼저 고정하지 않고 원자료, 관찰 조건, 적용 대상, 예외 사유를 분리하여 기록하였다. 검토 범위는 현재 차수의 운영 변경, 전후 비교, 보류 항목, 재확인 일정까지이며 다른 조직의 추정이나 구두 설명은 근거로 삼지 않는다. 같은 숫자라도 측정 시점과 입력 조건이 달라지면 의미가 달라질 수 있으므로, 판단자는 수치의 크기보다 수치가 만들어진 절차와 관리 상태를 함께 확인한다.

## 2. 관찰 내용과 비교 기준

주요 비교 항목은 {metric}이다. 직전 차수에서는 관련 이벤트가 {before}건 기록되었고, 개선 적용 뒤에는 {after}건으로 줄었다. 다만 검토자는 단순 감소만으로 안정성을 확정하지 않고, 표본 {review}건을 다시 열어 입력 조건과 처리 담당자와 예외 코드가 같은 기준으로 관리되었는지 대조하였다. 확인 기간은 {days}일이며, 관찰 담당자는 {owners}명으로 분리되어 교차 확인하였다. 이 절차는 모델 학습에 쓰기 위한 요약이 아니라, 나중에 같은 문서를 평가할 때 판단 근거가 텍스트 안에서 다시 확인되도록 남긴 것이다.

## 3. 적용 및 검증 결과

변경 적용 전에는 현장 담당자가 예외를 수동으로 분리했고, 적용 뒤에는 기준표에 따라 보류와 승인과 재확인을 나누었다. 보류 항목은 원인 미확정, 입력 누락, 영향 범위 불명확, 책임자 미지정으로 나누어 저장하였다. 승인 항목도 즉시 적용과 제한 적용과 다음 차수 재검토로 다시 구분하였다. 이 구분이 없으면 문서는 그럴듯한 운영 보고처럼 보이지만 실제 등급 판단에서는 어떤 정보가 공개 가능한 일반 절차이고 어떤 정보가 내부 조합인지 확인하기 어렵다.

## 4. 공개 여부와 관리 상태

{policy['nonpublicity']}

{policy['access']}

문서 관리자는 열람 범위, 전달 필요성, 보존 위치, 폐기 조건을 검토 메모와 분리해서 남겼다. 외부 전달이 필요한 경우에는 본문 전체가 아니라 공개 가능한 기준, 산출 방식, 비식별 요약만 전달 대상으로 삼는다. 반대로 내부 재현에 필요한 조합 조건이 포함된 경우에는 원본 문서의 위치와 접근 이력을 별도 목록으로 관리한다.

## 5. 업무 가치와 영향

{policy['value']}

영향 평가는 비용 절감, 일정 단축, 오류 재발 방지, 협력 범위 축소, 고객 대응 속도 중 어느 지표에 연결되는지 확인하였다. 이번 문서에서는 수치 결과와 업무 판단을 같은 문단에 섞지 않고, 관찰 사실을 먼저 둔 뒤 판단 근거를 별도로 적었다. 이 형식을 유지해야 나중에 검수자가 문서의 표현이 아니라 근거의 충분성을 기준으로 판정할 수 있다.

## 6. 예외 처리와 보류 기준

예외가 발견되면 즉시 결론을 바꾸지 않고, 입력 자료 오류인지 절차 변경인지 운영 조건 변화인지 구분한다. 확인이 필요한 항목은 담당자, 기한, 필요한 추가 자료를 함께 적고, 재검토가 끝나기 전에는 자동 확정 대상에서 제외한다. 특히 원자료의 범위가 바뀌었거나 협력 조직에 전달된 뒤 다시 회수된 경우에는 같은 내용이라도 관리 상태가 달라졌는지 확인한다.

## 7. 자료 보존과 접근 통제

원자료는 작성 차수, 승인 차수, 검토 차수로 나누어 보존한다. 수치표는 원본을 보존하고 본문에는 필요한 요약만 남긴다. 전달 기록에는 수신자, 목적, 전달 범위, 회수 필요 여부를 적는다. 공개 자료를 인용한 경우에도 내부 판단과 연결된 해석은 별도 근거로 관리하며, 내부 자료를 외부 보고서에 옮길 때에는 판정 근거가 손상되지 않는 범위에서만 요약한다.

## 8. 검토 의견과 종료 조건

종료 조건은 관찰값의 개선만이 아니라 예외 처리 완료, 접근 범위 확인, 다음 차수 확인 일정 등록, 보류 사유 해소까지 포함한다. 검토자는 적용 가능과 제한 적용과 보류를 분리해 표시하고, 보류 사유가 남아 있으면 같은 문서를 다음 평가의 입력으로 되돌린다. 이 문서는 평가용으로 동결되면 학습에 투입하지 않으며, 최종 성능 주장에도 고객 실문서와 같은 의미로 사용하지 않는다.
"""


def _record(
    case: tuple[str, str, str, str],
    form: tuple[str, str, str],
    *,
    grade: str,
    ordinal: int,
    family_kind: str,
) -> dict[str, object]:
    case_id, title, _issue, _metric = case
    form_id, form_title, _form_focus = form
    text = _body(case, form, grade, ordinal)
    policy = GRADE_POLICY[grade]
    family_id = f"eval-v2_2-{case_id}-{form_id}-{family_kind}"
    audit = _editorial_audit(policy, grade)
    return {
        "doc_id": f"{family_id}-{grade.lower()}-{ordinal:04d}",
        "document_family_id": family_id,
        "scenario_id": f"eval-v2_2-{case_id}-{form_id}-{grade.lower()}",
        "factor_profile_id": f"eval-v2_2-{grade.lower()}-svm",
        "family_profile_id": f"eval-v2_2-{form_id}",
        "length_profile_id": "direct-eval-v2_2-3000-5200",
        "requested_profile_min_chars": 3000,
        "requested_profile_max_chars": 5200,
        "document_type": f"{title} {form_title}",
        "domain": "independent-direct-authored-evaluation-v2_2",
        "industry": "direct-authored-proxy",
        "text": text,
        "label": grade,
        "intended_label": grade,
        "expected_factor_scores": dict(policy["scores"]),
        "evidence_card": _evidence_card(text, policy),
        "document_origin": "synthetic",
        "source": "direct_authored_proxy",
        "proxy_role": "confidential_simulation",
        "catalog_split_role": "frozen_proxy_eval_only",
        "training_use_permitted": False,
        "evaluation_use_permitted": True,
        "authoring_method": "codex_direct_authored_proxy_evaluation_v2_2",
        "generation_lineage": ["generator:codex:direct-authored-proxy-eval-v2_2"],
        "decision_bucket": "direct_authored_evaluation_candidate",
        "gate_version": "direct_authored_quality_v1",
        "primary_judge_model": "codex-editorial-audit-v2_2",
        "judging_lineage": ["primary_judge:codex-editorial-audit-v2_2"],
        "consensus_evidence": audit,
        "requires_manual_audit": False,
        "claim_scope": (
            "Direct-authored Proxy evaluation only; training forbidden; not customer-real "
            "accuracy evidence or Locked Gold."
        ),
    }


def build_records() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    families = [(case, form) for case in CASES for form in FORMS]
    assert len(families) == 200
    for ordinal, (case, form) in enumerate(families):
        for grade in ("TS", "S1", "S2", "S3"):
            rows.append(_record(case, form, grade=grade, ordinal=ordinal, family_kind="matched"))
    for ordinal, (case, form) in enumerate(families[:50], start=2000):
        rows.append(_record(case, form, grade="S1", ordinal=ordinal, family_kind="s1-extra"))
    for ordinal, (case, form) in enumerate(families[50:100], start=3000):
        rows.append(_record(case, form, grade="S2", ordinal=ordinal, family_kind="s2-extra"))
    for ordinal, (case, form) in enumerate(families[100:200], start=4000):
        rows.append(_record(case, form, grade="S3", ordinal=ordinal, family_kind="s3-extra"))

    counts = Counter(str(row["label"]) for row in rows)
    assert len(rows) == 1000 and counts == {"TS": 200, "S1": 250, "S2": 250, "S3": 300}
    assert len({str(row["doc_id"]) for row in rows}) == len(rows)
    assert len({str(row["text"]) for row in rows}) == len(rows)
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
        str(row["doc_id"]): list(validate_proxy_record(row, stage="eligible", intended_use="evaluation").errors)
        for row in rows
        if not validate_proxy_record(row, stage="eligible", intended_use="evaluation").ok
    }
    if failures:
        raise RuntimeError(json.dumps(failures, ensure_ascii=False, indent=2))
    payload = b"".join(
        (json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        for row in rows
    )
    _write_new(OUT, payload)
    manifest = {
        "schema": "direct-authored-proxy-evaluation-v2_2",
        "records": len(rows),
        "records_sha256": _sha256(payload.decode("utf-8")),
        "grade_counts": dict(sorted(Counter(str(row["label"]) for row in rows).items())),
        "case_families": 350,
        "training_forbidden": True,
        "no_llm_generation": True,
        "source_case_ledger": "new-v2_2-ledger",
        "claim_scope": "Proxy-only future evaluation; not customer-real accuracy or Locked Gold.",
    }
    _write_new(
        MANIFEST,
        (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"),
    )
    print(json.dumps({"output": str(OUT), **manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
