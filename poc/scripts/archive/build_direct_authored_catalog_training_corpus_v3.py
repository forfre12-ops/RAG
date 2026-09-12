"""Build the high-grade-diversified, catalog-bound Proxy training corpus v3.

No LLM is called.  The case ledger, document frames, and high-grade evidence
phrasing are directly authored here.  Its cases are disjoint from the v2.1
evaluation ledger; the resulting records are training-only and cannot be used
as a Golden Set or customer-real accuracy evidence.
"""

from __future__ import annotations

from collections import Counter, defaultdict
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
from scripts.build_direct_authored_catalog_training_corpus import _policy
from scripts.build_direct_authored_training_corpus import SHAPES, _shape_indices_for_grade
from scripts.build_direct_authored_training_pilot import _editorial_audit, _evidence_card
from scripts.build_proxy_scenarios import load_catalog


CATALOG = ROOT / "datasets" / "proxy_gold" / "training_scenario_catalog.v1.json"
OUT = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_6.jsonl"
MANIFEST = ROOT / "datasets" / "proxy_gold" / "direct_authored_catalog_training.v3_6.manifest.json"
FAMILY_COUNT = 225

# Explicitly distinct from both direct_authored_proxy_eval.v1 and v2.1.
TRAIN_CASES = (
    ("coating-window", "코팅 조건 전환 검토", "점도-온도-이송속도 결합값", "공정 전환 시 불량률을 낮추는 순서"),
    ("vaccine-yield", "백신 배양 수율 보정", "배양 시간-교반-영양액 투입 순서", "배치 실패 이력을 반영한 재현 조건"),
    ("subsea-cable", "해저 케이블 장애 복구", "구간별 감쇠값-우회 순서-절체 시점", "복구 시간을 줄이는 작업 조합"),
    ("customs-route", "통관 경로 예외 검토", "품목 조합-선적 시점-증빙 우선순위", "납기 지연을 회피하는 대응 순서"),
    ("forging-die", "단조 금형 수명 예측", "압력 곡선-냉각 간격-교체 순서", "금형 파손을 줄이는 현장 조건"),
    ("hospital-bed", "병상 배정 운영 변경", "환자 상태-격리 조건-전실 가용성", "응급 병상 확보를 위한 우선순위"),
    ("radar-filter", "레이더 잡음 제거 보정", "필터 계수-기상 조건-탐지 임계값", "오탐을 낮추는 신호 처리 조합"),
    ("mine-vent", "광산 환기 제어 검토", "풍량-가스 농도-작업 구역 순서", "위험 구간 진입을 막는 제어 조건"),
    ("foundry-pour", "주조 주입 조건 변경", "용탕 온도-주입 속도-금형 예열", "기공 결함을 줄이는 현장 절차"),
    ("telecom-roaming", "로밍 접속 품질 대응", "망 부하-가입자 군집-우회망 선택", "장애 확산을 막는 절체 판단"),
    ("hotel-pricing", "숙박 수요 배분 검토", "예약 흐름-취소율-객실 차단 기준", "수익 손실을 낮추는 배정 규칙"),
    ("pulp-bleach", "펄프 표백 공정 분석", "약품 농도-반응 시간-세척 순서", "백색도 편차를 줄이는 조합"),
    ("chip-package", "반도체 패키지 열변형 대응", "재료 조합-경화 시간-검사 위치", "미세 균열을 줄이는 검증 조건"),
    ("landslide-alert", "산사태 경보 기준 검토", "강우 누적-토양 수분-경사 변화", "경보 시점을 앞당기는 판단 규칙"),
    ("ecommerce-return", "반품 판정 우선순위", "상품 상태-구매 이력-재판매 가능성", "손실을 줄이는 회수·재배치 순서"),
    ("oxygen-blender", "의료가스 혼합 설비 점검", "혼합 비율-압력 차-교정 주기", "공급 안정성을 높이는 운영 기준"),
    ("solar-inverter", "태양광 인버터 이상 진단", "전압 파형-온도 이력-부품 상태", "출력 저하를 줄이는 정비 우선순위"),
    ("bridge-bearings", "교량 받침 상태 평가", "진동 기록-하중 변화-교체 시점", "장기 보수 비용을 낮추는 판단 근거"),
    ("language-routing", "다국어 상담 배정 검토", "문의 유형-상담 이력-언어 숙련도", "이탈을 줄이는 배정 방식"),
    ("cold-forge", "냉간 성형 금형 보정", "윤활 조건-하중 분포-가공 횟수", "표면 손상을 줄이는 세부 절차"),
    ("forest-fire", "산불 감시 경로 조정", "풍향 변화-연료 밀도-접근 시간", "초기 대응 시간을 줄이는 배치 기준"),
    ("surgical-kit", "수술 키트 구성 변경", "소모품 조합-준비 순서-오염 방지 조건", "재준비 시간을 줄이는 관리 절차"),
    ("loan-workout", "여신 회수 전략 검토", "상환 이력-담보 상태-협상 순서", "손실 회피를 위한 우선 조치"),
    ("glass-anneal", "유리 어닐링 편차 분석", "온도 구간-냉각 속도-적재 위치", "파손률을 낮추는 세부 조건"),
    ("autonomous-fleet", "자율 운송 관제 기준", "경로 위험도-적재 상태-통신 지연", "사고 가능성을 낮추는 개입 규칙"),
    ("library-archive", "기록물 보존 환경 검토", "습도 편차-보관 위치-복원 우선순위", "손상 확산을 막는 관리 기준"),
    ("lpg-blending", "가스 혼합 비율 검토", "성분 비율-압력 조건-공급 순서", "설비 이상을 줄이는 운전 조합"),
    ("aqua-feed", "양식 사료 배합 변경", "수온-사료 조성-급이 간격", "성장 편차를 줄이는 운영 순서"),
    ("court-schedule", "소송 일정 대응 검토", "증거 상태-상대 주장-제출 순서", "절차상 손실을 줄이는 내부 대응"),
    ("museum-light", "전시 조도 관리 검토", "조도 값-노출 시간-작품 상태", "보존 위험을 낮추는 교체 기준"),
)

FRAMES = (
    "변경 검토서", "현장 조치 기록", "재현성 검증 계획", "예외 승인 메모",
    "품질 이슈 분석", "협력사 전달 검토", "운영 결정 회의록", "시험 결과 해석서",
    "사후 점검 보고", "위험 대응 시나리오", "접근권한 점검서", "사업 영향 분석서",
)

HIGH_GRADE_CONTEXT = {
    "TS": "이 조합은 외부에 공개되지 않은 설계·제어 조건과 실패 이력을 함께 포함하며, 핵심 공정 또는 사업 기회에 직접 영향을 준다.",
    "S1": "이 조합은 일반 원칙을 넘어 실제 현장 조건과 예외 순서를 함께 담아 재현에 상당한 시행착오가 필요한 비공개 운영정보다.",
    "S2": "내부 운영상 참고가 되는 정보이나 일반 원칙과 공개된 절차만으로도 상당 부분 이해할 수 있어 고등급 핵심정보로 단정하지 않는다.",
    "S3": "공개 규격과 일반 업무 절차의 적용 결과를 정리한 자료로, 비공개 결합조건이나 고유 실패 이력은 포함하지 않는다.",
}

COMMON_CONTROL = """
## 자료 관리와 접근 통제

검토에 사용한 원자료는 작성 시점, 확인 책임자, 변경 사유, 전달 범위를 분리해 기록한다. 단일 수치가 목표 범위 안에 있더라도 입력 조건·측정 방법·적용 대상을 함께 확인하지 않으면 같은 결과로 해석하지 않는다. 예외가 발견되면 임시 조치와 복구 기준을 분리해 기록하고, 다음 확인 시점에 원자료와 판단 근거를 다시 대조한다.

## 종료 기준

종료는 조치 실행만으로 선언하지 않는다. 검증 증적, 변경 이력, 전달 범위, 미결 항목, 후속 책임자를 모두 확인한 뒤에만 종료로 기록한다. 서로 다른 판단이 있으면 다수결로 덮지 않고 근거와 적용 범위를 다시 확인한다.
""".strip()

VERIFICATION_APPENDIX = """
## 세부 검증 절차

검증 담당자는 기준값을 확인할 때 원자료의 생성 시점, 측정 장비 상태, 입력 조건, 적용 대상, 담당자 인수인계 상태를 함께 대조한다. 첫 번째 대조는 변경 전 상태를 확인하기 위한 것이고, 두 번째 대조는 변경 후 조건이 같은지 확인하기 위한 것이다. 두 결과가 다르면 평균값으로 숨기지 않고 차이가 발생한 구간과 가능한 원인을 별도 항목으로 남긴다.

업무상 긴급한 적용이 필요한 경우에도 적용 범위, 유효기간, 되돌림 조건, 재검토 책임자를 기록한다. 특정 조치가 다른 부서·협력사·고객 업무에 영향을 줄 수 있으면 전달 범위를 먼저 확인하고, 전달 후에는 수신자·목적·회수 여부를 이력으로 남긴다. 승인되지 않은 복사본·요약본·화면 캡처는 원문과 같은 보호 수준으로 취급하며, 필요성이 끝난 자료는 정해진 절차에 따라 회수 또는 폐기한다.

## 판단의 재현성

다음 검토자는 이전 결론만 보지 않고 관찰 사실, 사용한 기준, 예외 판단, 접근 기록을 따라 같은 결론에 도달할 수 있어야 한다. 따라서 작성자는 결론을 뒷받침하지 않는 정보도 삭제하지 않고 관련성·한계·확인 필요성을 표시한다. 이력의 일부가 빠졌거나 승인 근거가 확인되지 않으면 현재 판단은 확정이 아니라 보류로 전환하며, 보완 후 새 이력에서 다시 판단한다.
""".strip()

RETENTION_APPENDIX = """
## 검토자료 보관

검토에 사용한 표, 사진, 원시 기록, 승인 메시지는 본문 결론과 연결 가능한 식별자로 보관한다. 보관 위치가 바뀌면 이전 위치·이동 사유·새 접근 권한을 함께 기록하고, 참조만 필요한 자료와 수정 가능한 원자료를 구분한다.
""".strip()


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _case(base: tuple[str, str, str, str], scenario: dict[str, object]) -> dict[str, str]:
    case_id, title, combination, outcome = base
    document_type = str(scenario["document_type"])
    domain = str(scenario["domain"])
    return {
        "case_id": case_id,
        "document_type": document_type,
        "context": f"{title} 업무에서 {domain} 영역의 {document_type}를 작성하는 상황",
        "detail": f"{combination} 및 {outcome}",
        "metric": "3개 조건군을 2회씩 대조하고 24시간 경과 후 재확인한 결과",
        "public": "공개 규격·공개 안내자료·일반 업무 기준",
        "roles": "현장 책임자, 검증 담당자, 변경 승인자",
        "base_title": title,
        "outcome": outcome,
    }


def _text(case: dict[str, str], policy: dict[str, object], grade: str, ordinal: int, frame: str) -> str:
    high_context = HIGH_GRADE_CONTEXT[grade]
    disclosure = str(policy["disclosure"])
    if grade == "S3":
        disclosure += f" {case['public']}에 근거한다."
    return f"""# {case['document_type']}

문서 형식: {frame} / 기록 번호: TR3-{case['case_id'].upper()}-{ordinal + 1:04d}

## 변경 안건과 검토 범위

본 문서는 {case['context']}에 대한 판단 기록이다. 이번 차수의 중심 대상은 {case['detail']}이며, 담당자는 관찰 사실·판단 근거·후속 조치를 섞지 않고 각각 기록한다. 적용 전에는 대상 설비 또는 업무 범위, 이전 이력, 예외 조건을 확인하고, 적용 후에는 같은 기준으로 결과를 다시 대조한다.

## 적용 및 검증 결과

{case['metric']} 기준값과의 차이를 확인했다. 첫 번째 확인에서는 입력 조건과 담당 역할을 기록하고, 두 번째 확인에서는 변경 전후의 결과가 같은 측정 기준에서 나왔는지 대조했다. 수치가 일치하지 않거나 증적이 부족한 항목은 완료로 처리하지 않고 보완 목록으로 분리했다. {case['roles']}가 각 단계의 판단과 조치 이력을 남긴다.

## 등급 판단 근거

{high_context}

공개성 근거: {disclosure}

사업상 가치 근거: {policy['value']}

관리 근거: {policy['access']}

## 검토 의견 및 결정

검토자는 {case['outcome']}에 미치는 영향을 고려하되, 기대 효과만으로 결론을 내리지 않는다. 예외가 발생하면 영향 범위, 임시 조치, 복구 조건, 재확인 담당자를 분리해 남긴다. 외부 또는 협력사에 전달할 필요가 있으면 업무 수행에 필요한 최소 범위와 전달 이력을 기록하고, 원자료의 수정·복사는 승인된 절차 안에서만 수행한다.

{COMMON_CONTROL}

{VERIFICATION_APPENDIX}

{RETENTION_APPENDIX}

## 작성·검토·승인 이력

작성자는 관찰값과 출처를 연결하고, 검토자는 공개성·가치·관리 근거가 서로 모순되지 않는지 확인한다. 승인자는 적용 범위와 종료 기준을 확정하며, 이후 발견된 사실은 기존 기록을 덮어쓰지 않고 새 이력으로 남긴다.
""".strip() + "\n"


def build_records() -> tuple[list[dict[str, object]], dict[str, object]]:
    catalog, scenarios = load_catalog(CATALOG)
    planned: dict[str, list[dict[str, object]]] = defaultdict(list)
    for scenario in sorted(scenarios, key=lambda value: str(value["scenario_id"])):
        planned[str(scenario["label"])].extend([scenario] * int(scenario["target_count"]))
    targets = {"TS": 750, "S1": 750, "S2": 750, "S3": 450}
    assert {grade: len(items) for grade, items in planned.items()} == targets
    rows: list[dict[str, object]] = []
    for grade, items in planned.items():
        for ordinal, (scenario, shape_index) in enumerate(zip(items, _shape_indices_for_grade(grade, len(items)), strict=True)):
            scores = {key: int(value) for key, value in dict(scenario["expected_factor_scores"]).items()}
            policy = _policy(scores)
            case = _case(TRAIN_CASES[ordinal % len(TRAIN_CASES)], scenario)
            frame = FRAMES[shape_index]
            text = _text(case, policy, grade, ordinal, frame)
            shape_id, _canonical_shape_name, length_name = SHAPES[shape_index]
            row = {
                "doc_id": f"direct-catalog-v3-{grade.lower()}-{ordinal + 1:04d}",
                "document_family_id": f"direct-catalog-v3-family-{ordinal % FAMILY_COUNT:03d}",
                "scenario_id": str(scenario["scenario_id"]),
                "factor_profile_id": str(scenario["factor_profile_id"]),
                # Keep the immutable pool's canonical shape/length keys for
                # quota selection; the document body still carries the
                # independently authored v3 form name above.
                "family_profile_id": f"direct-{shape_id}",
                "length_profile_id": f"direct-{length_name}",
                "requested_profile_min_chars": 2200,
                "requested_profile_max_chars": 3200,
                "document_type": str(scenario["document_type"]),
                "domain": str(scenario["domain"]),
                "industry": "direct-authored-proxy",
                "text": text,
                "label": grade,
                "intended_label": grade,
                "expected_factor_scores": scores,
                "evidence_card": _evidence_card(text, policy, case),
                "document_origin": "synthetic",
                "source": "direct_authored_proxy",
                "proxy_role": "confidential_simulation",
                "catalog_split_role": "train_pool_only",
                "training_use_permitted": True,
                "evaluation_use_permitted": False,
                "authoring_method": "codex_direct_authored_high_grade_diverse_v3",
                "generation_lineage": ["generator:codex:direct-authored-catalog-training-v3"],
                "decision_bucket": "direct_authored_training_candidate",
                "gate_version": "direct_authored_quality_v1",
                "primary_judge_model": "codex-editorial-audit-v1",
                "judging_lineage": ["primary_judge:codex-editorial-audit-v1"],
                "consensus_evidence": _editorial_audit(policy, grade),
                "requires_manual_audit": False,
                "claim_scope": "Direct-authored Proxy training only; not customer-real evidence, golden evaluation, or Locked Gold.",
            }
            rows.append(row)
    assert len(rows) == 2700 and Counter(str(row["label"]) for row in rows) == targets
    assert len({str(row["text"]) for row in rows}) == len(rows)
    return rows, catalog


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
    rows, catalog = build_records()
    failures = [row["doc_id"] for row in rows if not validate_proxy_record(row, stage="eligible", intended_use="training").ok]
    if failures:
        raise RuntimeError(f"invalid records: {failures[:20]}")
    payload = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8") for row in rows)
    _write_new(OUT, payload)
    manifest = {
        "schema": "direct-authored-catalog-training-v3",
        "records": len(rows),
        "records_sha256": _sha256(payload),
        "grade_counts": dict(sorted(Counter(str(row["label"]) for row in rows).items())),
        "factor_profiles": len({str(row["factor_profile_id"]) for row in rows}),
        "scenarios": len({str(row["scenario_id"]) for row in rows}),
        "catalog_version": catalog["version"],
        "catalog_sha256": _sha256(CATALOG.read_bytes()),
        "training_only": True,
        "no_llm_generation": True,
        "evaluation_case_ledger": "disjoint_from_direct_authored_proxy_eval_v2_1",
    }
    _write_new(MANIFEST, (json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8"))
    print(json.dumps({"output": str(OUT), **manifest}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
