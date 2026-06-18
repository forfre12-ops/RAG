"""정본 가이드 B안 — 등급 = S×V×M 곱셈 산정 단위 테스트.

근거: doc/22 v2 §1.2 + 영업비밀 등급분류 가이드 11~12p (워크드 예시).
순수 함수 테스트 — DB·임베딩·외부 의존 없음.
"""

from __future__ import annotations

import pytest

from lloydk.modules.m3_labeling.rule_engine import grade_from_svm
from lloydk.modules.m3_labeling.seeds import to_canonical_factor


# 가이드 12p 워크드 예시 (정답지)
@pytest.mark.parametrize(
    "doc,s,v,m,expected",
    [
        ("조직도", 1, 1, 1, "S2"),          # 곱 1 → 2급 대외비
        ("내선번호", 0, 0, 0, "S3"),         # 곱 0 → 3급 공개
        ("인사평가보고서", 1, 2, 2, "S1"),   # 곱 4 → 1급 비밀
        ("중장기경영계획", 2, 2, 2, "TS"),   # 곱 8 → 특급기밀
        ("사무실배치도", 1, 1, 0, "S3"),     # M=0 → 곱 0 → 공개
    ],
)
def test_guide_worked_examples(doc, s, v, m, expected):
    assert grade_from_svm(s, v, m) == expected, doc


def test_secrecy_gate_zero_forces_public():
    """비공지성(S)=0이면 V·M이 최대여도 공개(곱셈 게이트 = 부정경쟁방지법 §2.2)."""
    assert grade_from_svm(0, 2, 2) == "S3"


def test_all_possible_products_map_to_four_grades():
    """각 요소 0/1/2 → 가능한 곱값 {0,1,2,4,8} 전수 매핑 검증."""
    assert grade_from_svm(0, 1, 2) == "S3"  # 0
    assert grade_from_svm(1, 1, 1) == "S2"  # 1
    assert grade_from_svm(1, 1, 2) == "S2"  # 2
    assert grade_from_svm(1, 2, 2) == "S1"  # 4
    assert grade_from_svm(2, 2, 2) == "TS"  # 8


def test_legacy_factor_alias_maps_to_three_requisites():
    """레거시 4요소 태그가 정본 3요건으로 정규화된다(시드 재태깅 불요)."""
    assert to_canonical_factor("NON_PUBLICITY") == "SECRECY"
    assert to_canonical_factor("ECONOMIC_VALUE") == "VALUE"
    assert to_canonical_factor("MANAGEMENT_LEVEL") == "MANAGEMENT"
    assert to_canonical_factor("LEAK_IMPACT") == "VALUE"
    assert to_canonical_factor("SECRECY") == "SECRECY"  # 이미 정본이면 그대로
