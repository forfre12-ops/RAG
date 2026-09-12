"""witness taxonomy v1 — constructed_floor 트랙의 등급 결정 사실(witness) 카탈로그.

설계 원칙(2026-07-03 5기둥 적대검증 salvage):
  - witness = 등급 floor를 결정하는 **구체적 사실 문장**. 문서는 이 사실을 원문 그대로
    포함해야 하고, 검증은 LLM이 아닌 결정적 코드(constructed_floor.check_witness)로 한다.
  - 권위 계층화: TS witness의 basis는 외부 열거(산업기술보호법 §9 고시·방위산업기술보호법)
    인용 필수 — 자사 정의만으로 TS 주장 금지. S1/S2의 basis는 부정경쟁방지법 §2-2호 +
    등급 가이드 v2.2의 S/V 레벨 근거(taxonomy 저자=Koipa 판단임을 숨기지 않는다 —
    이 카탈로그가 감사 가능한 산출물이 되는 것이 목적).
  - 도메인→등급 shortcut 차단(714건 누출 85.6% 교훈): S1과 S2는 **같은 도메인 집합**
    (finance/sales/tech/hr)을 공유하고 V(피해 크기) 레벨만 다르다 — 도메인으로 등급을
    맞출 수 없다. TS는 법 자체가 도메인 열거라 도메인 잠금이 불가피(basis가 곧 도메인).
  - 슬롯 값은 인덱스의 결정적 함수(instantiate_witness) — 재현 가능·무작위 없음.

⚠️ 이 카탈로그의 라벨 의미는 전부 **floor**(≥등급)다. exact-grade 아님.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class WitnessSpec:
    """witness 유형 1건 — token_template의 {v1},{v2}는 instantiate_witness가 채운다."""

    witness_id: str
    grade: str            # floor 등급 (TS|S1|S2)
    domain: str
    name: str
    token_template: str   # 문서에 verbatim 포함될 사실 문장 템플릿
    basis: str            # 법령/가이드 인용 — floor 주장의 근거(감사 추적)
    factor_profile: str   # S/V/M 관점 근거 서술(등급 가이드 v2.2 좌표)


# ── TS: 외부 열거(§9 고시·방산법) 인용 필수 — 법이 도메인을 열거하므로 도메인 잠금 불가피 ──
_TS = [
    WitnessSpec(
        witness_id="ts_semi_etch",
        grade="TS", domain="tech",
        name="반도체 미세공정 식각 레시피",
        token_template="3nm 공정 식각 가스 유량비 SF6:CHF3 = {v1}:{v2} 및 RF 파워 {v3}W 설정값",
        basis="산업기술보호법 §9 · 산업통상자원부 고시 제2023-209호 반도체 1호(10nm 이하 제조 공정 기술)",
        factor_profile="S=2(국가핵심기술 실체) V=2(유출 시 공정窓 재현 가능) — 고시 열거로 TS floor",
    ),
    WitnessSpec(
        witness_id="ts_battery_electrolyte",
        grade="TS", domain="tech",
        name="이차전지 전해질 조성",
        token_template="전고체 전해질 Li2S:P2S5:LiCl 몰비 {v1}:{v2}:{v3} 및 소성 온도 {v4}°C 프로파일",
        basis="산업기술보호법 §9 · 고시 제2023-209호 이차전지 분야 국가핵심기술",
        factor_profile="S=2 V=2 — 고시 열거 조성·공정 파라미터로 TS floor",
    ),
    WitnessSpec(
        witness_id="ts_defense_seeker",
        grade="TS", domain="defense",
        name="유도무기 탐색기 파라미터",
        token_template="탐색기 CFAR 임계 {v1}dB 및 표적 추적 갱신 주기 {v2}ms 설정",
        basis="방위산업기술보호법 §3(방위산업기술 지정) · 산업기술보호법 §9",
        factor_profile="S=2 V=2 — 방산기술 지정 대상 파라미터로 TS floor",
    ),
    WitnessSpec(
        witness_id="ts_space_engine",
        grade="TS", domain="tech",
        name="우주발사체 터보펌프 설계값",
        token_template="터보펌프 회전수 {v1}RPM 및 연소압 {v2}bar 설계 마진 수치",
        basis="산업기술보호법 §9 · 고시 제2023-209호 항공우주 분야 국가핵심기술",
        factor_profile="S=2 V=2 — 고시 열거 발사체 핵심 설계값으로 TS floor",
    ),
]

# ── S1/S2: 같은 도메인 집합 공유(finance/sales/tech/hr) — V 레벨만 다르다 ──────────────
_S1 = [
    WitnessSpec(
        witness_id="s1_finance_ma",
        grade="S1", domain="finance",
        name="미공개 인수 협상 상한",
        token_template="인수 협상 상한가 주당 {v1}원 및 CFO 승인 워크스루 {v2}단계 조건",
        basis="부정경쟁방지법 §2조2호 · 등급 가이드 v2.2 S=2·V=2(유출 시 협상 결렬·중대 손해)",
        factor_profile="S=2(비공개 거래 조건) V=2(거래 자체 무산 가능) — S1 floor",
    ),
    WitnessSpec(
        witness_id="s1_sales_customerdb",
        grade="S1", domain="sales",
        name="핵심 고객 이탈확률 모델 산출값",
        token_template="상위 거래처 이탈확률 상위 {v1}개사 명단과 갱신 확률 {v2}% 산출 테이블",
        basis="부정경쟁방지법 §2조2호(고객정보 영업비밀성 인정 유형) · 가이드 v2.2 S=2·V=2",
        factor_profile="S=2(비공개 고객 분석) V=2(경쟁사 이관 시 매출 직접 타격) — S1 floor",
    ),
    WitnessSpec(
        witness_id="s1_tech_source_access",
        grade="S1", domain="tech",
        name="코어 모듈 접근권한 매트릭스",
        token_template="결제 코어 모듈 저장소 접근권한 보유자 {v1}명 명단과 특권 토큰 교체 주기 {v2}일",
        basis="부정경쟁방지법 §2조2호 · 가이드 v2.2 S=2·V=2(침해 시 시스템 전체 노출)",
        factor_profile="S=2(내부 통제 정보) V=2(악용 시 중대 침해) — S1 floor",
    ),
    WitnessSpec(
        witness_id="s1_hr_exec_comp",
        grade="S1", domain="hr",
        name="임원 성과보상 산식",
        token_template="임원 성과급 산식 계수 {v1} 및 비공개 스톡옵션 행사가 {v2}원 조건",
        basis="부정경쟁방지법 §2조2호 · 가이드 v2.2 S=2·V=2(유출 시 핵심인력 유출 연쇄)",
        factor_profile="S=2 V=2 — S1 floor",
    ),
]

_S2 = [
    WitnessSpec(
        # 명칭에 '원가 구조'를 쓰지 않는다 — 룰 시드의 S1 키워드('원가 구조')와 표면형이
        # 겹치면 상위등급 negative scan이 정당하게 탈락시킨다(실제로 v1 초안이 걸렸음 —
        # veto가 taxonomy 자체의 등급 간 어휘 충돌을 잡아낸 사례).
        witness_id="s2_finance_cost",
        grade="S2", domain="finance",
        name="부품 단가 내역",
        token_template="주력 제품 부품당 단가 {v1}원 및 협력사 마진 {v2}% 테이블",
        basis="등급 가이드 v2.2 S=2·V=1(경쟁상 불이익 수준)",
        factor_profile="S=2(내부 단가) V=1(불이익이나 치명 아님) — S2 floor",
    ),
    WitnessSpec(
        witness_id="s2_sales_pipeline",
        grade="S2", domain="sales",
        name="분기 영업 파이프라인",
        token_template="분기 파이프라인 예상 수주액 {v1}억원 및 성사 확률 {v2}% 내부 추정",
        basis="등급 가이드 v2.2 S=2·V=1",
        factor_profile="S=2(내부 추정치) V=1 — S2 floor",
    ),
    WitnessSpec(
        witness_id="s2_tech_process_memo",
        grade="S2", domain="tech",
        name="라인 개선 파라미터 메모",
        token_template="포장 라인 사이클타임 {v1}초→{v2}초 단축 세팅 변경값",
        basis="등급 가이드 v2.2 S=2·V=1(공정 개선 노하우, 파급 한정)",
        factor_profile="S=2 V=1 — S2 floor",
    ),
    WitnessSpec(
        witness_id="s2_hr_reorg",
        grade="S2", domain="hr",
        name="조직 개편 일정",
        token_template="{v1}월 조직 개편안 초안과 부서 통합 대상 {v2}개 팀 목록",
        basis="등급 가이드 v2.2 S=2·V=1",
        factor_profile="S=2(내부 인사 계획) V=1 — S2 floor",
    ),
]

WITNESS_TYPES: list[WitnessSpec] = _TS + _S1 + _S2


def instantiate_witness(spec: WitnessSpec, index: int) -> str:
    """슬롯 값을 index의 결정적 함수로 채운 witness 사실 문장(무작위 없음·재현 가능)."""
    i = int(index)
    values = {
        "v1": str(11 + (i * 7) % 89),
        "v2": str(23 + (i * 13) % 71),
        "v3": str(150 + (i * 37) % 850),
        "v4": str(400 + (i * 53) % 300),
    }
    out = spec.token_template
    for k, v in values.items():
        out = out.replace("{" + k + "}", v)
    return out


def specs_by_grade(grade: str | None = None) -> list[WitnessSpec]:
    if grade is None:
        return list(WITNESS_TYPES)
    g = str(grade).upper()
    return [s for s in WITNESS_TYPES if s.grade == g]
