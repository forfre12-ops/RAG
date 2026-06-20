"""정본 가이드 B안 v2.2 — 등급 = S×V×M 곱셈 산정 단위 테스트.

근거: doc/22 v2.2 §4.5~§4.6 + 영업비밀 등급분류 가이드 11~12p (워크드 예시).
v2.2 핵심 변경: s==2 AND v==2 분기 선행 → m=0이어도 S1 확정(관리 미공식화 고가치 영업비밀).
순수 함수 테스트 — DB·임베딩·외부 의존 없음.
"""

from __future__ import annotations

import pytest

from lloydk.modules.m3_labeling.rule_engine import grade_from_svm, svm_levels_for_grade
from lloydk.modules.m3_labeling.seeds import to_canonical_factor


# 가이드 12p 워크드 예시 (v2.2 기준 재검토)
@pytest.mark.parametrize(
    "doc,s,v,m,expected",
    [
        ("조직도", 1, 1, 1, "S2"),           # 곱 1 → 2급 대외비
        ("내선번호", 0, 0, 0, "S3"),          # 곱 0 → 3급 공개
        # v2.2 변경: (1,2,2)→곱=4이나 s=1<2 → s==2 AND v==2 분기 미진입 → 곱≥1 → S2
        # (구 가이드 12p는 product=4→S1이었으나, v2.2는 s==2·v==2인 경우만 S1/TS 진입)
        ("인사평가보고서", 1, 2, 2, "S2"),    # s=1 → 분기 불통 → 곱≥1 → S2
        ("중장기경영계획", 2, 2, 2, "TS"),    # s==2 AND v==2, 곱=8≥4 → TS
        ("사무실배치도", 1, 1, 0, "S3"),      # s=1, 곱=0 → S3
    ],
)
def test_guide_worked_examples(doc, s, v, m, expected):
    assert grade_from_svm(s, v, m) == expected, doc


def test_secrecy_gate_zero_forces_public():
    """비공지성(S)=0이면 V·M이 최대여도 공개(곱셈 게이트 = 부정경쟁방지법 §2.2)."""
    assert grade_from_svm(0, 2, 2) == "S3"


def test_v22_s1_edge_case():
    """v2.2 핵심: s=2·v=2·m=0 → S1 확정(관리 미공식화 고가치 영업비밀, doc/22 §4.5)."""
    assert grade_from_svm(2, 2, 0) == "S1"
    # S1 vs TS 분기: m=0→S1, m≥1→TS
    assert grade_from_svm(2, 2, 1) == "TS"
    assert grade_from_svm(2, 2, 2) == "TS"


def test_v22_s1_canonical_levels():
    """svm_levels_for_grade('S1') → (2,2,0) — v2.2 S1 표준형, grade_from_svm 왕복 정합."""
    s, v, m = svm_levels_for_grade("S1")
    assert (s, v, m) == (2, 2, 0)
    assert grade_from_svm(s, v, m) == "S1"


def test_all_reachable_svm_combinations():
    """0/1/2 범위 대표 조합 전수 검증 (v2.2 기준)."""
    assert grade_from_svm(0, 1, 2) == "S3"   # 곱 0, s=0 → S3
    assert grade_from_svm(1, 1, 0) == "S3"   # 곱 0, s=1 → S3
    assert grade_from_svm(1, 1, 1) == "S2"   # 곱 1 → S2
    assert grade_from_svm(1, 1, 2) == "S2"   # 곱 2 → S2
    assert grade_from_svm(1, 2, 2) == "S2"   # 곱 4, s=1≠2 → S2 (v2.2 변경: 구 S1)
    assert grade_from_svm(2, 2, 0) == "S1"   # s==2 AND v==2, 곱<4 → S1 (v2.2 신규)
    assert grade_from_svm(2, 2, 1) == "TS"   # s==2 AND v==2, 곱=4≥4 → TS
    assert grade_from_svm(2, 2, 2) == "TS"   # s==2 AND v==2, 곱=8≥4 → TS


def test_legacy_factor_alias_maps_to_three_requisites():
    """레거시 4요소 태그가 정본 3요건으로 정규화된다(시드 재태깅 불요)."""
    assert to_canonical_factor("NON_PUBLICITY") == "SECRECY"
    assert to_canonical_factor("ECONOMIC_VALUE") == "VALUE"
    assert to_canonical_factor("MANAGEMENT_LEVEL") == "MANAGEMENT"
    assert to_canonical_factor("LEAK_IMPACT") == "VALUE"
    assert to_canonical_factor("SECRECY") == "SECRECY"  # 이미 정본이면 그대로
