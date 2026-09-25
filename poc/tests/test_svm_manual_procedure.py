"""S/V/M 절차형 판단 계산기 — 가이드 원문 예시와 M-미확인 대체 경로를 검증한다."""

from __future__ import annotations

import pytest

from koipa.modules.m3_labeling.svm_manual_procedure import evaluate


# 가이드 12쪽 워크드 예시(원문 표) — 그대로 재현돼야 한다.
@pytest.mark.parametrize("s,v,m,expected_grade", [
    (1, 1, 1, "S2"),  # 조직도 → 대외비
    (0, 0, 0, "S3"),  # 내선번호 → 일반공개정보
    (1, 2, 2, "S1"),  # 인사 평가 보고서 → 비밀
    (2, 2, 2, "TS"),  # 중장기 경영계획 → 극비
    (1, 1, 0, "S3"),  # 사무실 배치도 → (표에서 "-", product=0=공개와 동일)
])
def test_guide_worked_examples_reproduced(s, v, m, expected_grade):
    result = evaluate(s, v, m)
    assert result.grade == expected_grade
    assert result.formula_used == "guide_full_svm"


def test_m_confirmed_zero_is_not_rescued():
    """M 이 '미확인'이 아니라 '0으로 확인'되면 가이드 순수곱셈을 그대로 따른다(공개로 떨어짐).

    이건 위험해 보이지만 가이드 원문 그대로다 — 안전장치는 M=0 을 확정 못 했을 때만 켠다.
    """
    result = evaluate(2, 2, 0)
    assert result.grade == "S3"
    assert result.formula_used == "guide_full_svm"


def test_m_unknown_uses_s_v_only_fallback_not_forced_zero():
    """M 을 모르면 0으로 채우지 않는다 — S·V 단독 중 더 민감한 쪽을 취한다."""
    result = evaluate(2, 2, None)
    assert result.formula_used == "s_v_only_fallback"
    assert result.grade == "S1", "S=2,V=2 라면 M 을 몰라도 최소 S1 로는 남아야 한다(공개로 떨어지면 안 됨)"


@pytest.mark.parametrize("s,v,expected_grade", [
    (0, 0, "S3"),
    (2, 0, "S1"),  # S단독=S1, V단독=S3 -> 더 민감한 S1
    (0, 2, "S1"),  # 대칭 확인
    (1, 1, "S2"),
])
def test_s_v_only_fallback_takes_the_more_sensitive_side(s, v, expected_grade):
    result = evaluate(s, v, None)
    assert result.grade == expected_grade
    assert result.formula_used == "s_v_only_fallback"


@pytest.mark.parametrize("bad", [-1, 3, 99])
def test_out_of_range_levels_are_rejected(bad):
    with pytest.raises(ValueError):
        evaluate(bad, 1, 1)
    with pytest.raises(ValueError):
        evaluate(1, bad, 1)
    with pytest.raises(ValueError):
        evaluate(1, 1, bad)


def test_note_explains_which_path_was_taken():
    with_m = evaluate(1, 1, 1)
    without_m = evaluate(1, 1, None)
    assert "순수곱셈" in with_m.note
    assert "M 미확인" in without_m.note
