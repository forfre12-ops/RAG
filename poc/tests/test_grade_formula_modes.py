"""등급 산정식 세 모드 — 27조합 전수 잠금.

왜(2026-09-13). 적용서 v2.2 §3.7 이 "가이드 순수 곱셈을 기본값으로 두고 v2.2 보정을
운영 토글로 분리한다 — 하드코딩 금지·설정 주도" 를 권고했는데 산정식만 코드에
박혀 있었다. 고객사마다 아픈 곳이 다르다 — 과분류가 문제인 곳과 미탐이 문제인 곳.

세 모드가 서로 **다르게** 동작하는지, 그리고 기본값이 현행과 **완전히 같은지**를 잠근다.
기본값이 흔들리면 배포본 판정이 조용히 바뀐다.
"""
from __future__ import annotations

import itertools

import pytest

from koipa.modules.m3_labeling.rule_engine import grade_from_svm

LEVELS = (0, 1, 2)
ALL = list(itertools.product(LEVELS, LEVELS, LEVELS))

# 현행 v2.2 — 이 표가 배포본 판정이다. 바뀌면 회귀다.
V22 = {
    (s, v, m): ("TS" if s * v * m >= 4 else "S1") if (s == 2 and v == 2)
    else ("S2" if s * v * m >= 1 else "S3")
    for s, v, m in ALL
}
# 발주처 가이드 p12 순수 곱셈 — 0→S3 · 1·2→S2 · 4→S1 · 8→TS
GUIDE_MAP = {0: "S3", 1: "S2", 2: "S2", 4: "S1", 8: "TS"}


def test_default_mode_matches_current_deployment() -> None:
    """기본값은 현행과 27/27 동일해야 한다 — 설정을 안 건드리면 판정이 안 바뀐다."""
    for combo in ALL:
        assert grade_from_svm(*combo) == V22[combo], f"{combo} 에서 기본 모드가 현행과 다르다"


def test_guide_mode_is_pure_product_mapping() -> None:
    for s, v, m in ALL:
        assert grade_from_svm(s, v, m, mode="guide") == GUIDE_MAP[s * v * m]


def test_fnr_mode_keeps_high_value_unmanaged_secret() -> None:
    """(2,2,0) = 고가치·미관리 비밀. guide 는 공개로 떨구지만 fnr 은 S1 로 지킨다."""
    assert grade_from_svm(2, 2, 0, mode="guide") == "S3"
    assert grade_from_svm(2, 2, 0, mode="fnr") == "S1"
    # 나머지는 guide 와 같아야 한다 — fnr 은 그 한 칸만 보정한다
    for s, v, m in ALL:
        if (s, v, m) == (2, 2, 0):
            continue
        assert grade_from_svm(s, v, m, mode="fnr") == GUIDE_MAP[s * v * m]


def test_v22_and_guide_differ_in_exactly_four_combos() -> None:
    """실측 2026-09-13: 4개. 늘거나 줄면 어느 한쪽이 바뀐 것이다."""
    diff = [c for c in ALL if grade_from_svm(*c, mode="v22") != grade_from_svm(*c, mode="guide")]
    assert sorted(diff) == [(1, 2, 2), (2, 1, 2), (2, 2, 0), (2, 2, 1)], diff


def test_v22_is_lower_on_two_combos() -> None:
    """v22 가 가이드보다 **낮게** 보는 두 조합 — 미탐 방향이라 따로 잠근다."""
    order = {"TS": 0, "S1": 1, "S2": 2, "S3": 3}
    for combo in ((1, 2, 2), (2, 1, 2)):
        v22 = grade_from_svm(*combo, mode="v22")
        guide = grade_from_svm(*combo, mode="guide")
        assert order[v22] > order[guide], f"{combo}: v22={v22} guide={guide}"


def test_unknown_mode_falls_back_to_v22(caplog: pytest.LogCaptureFixture) -> None:
    """모르는 모드가 와도 분류를 멈추지 않는다 — 현행으로 폴백하고 경고만 남긴다."""
    for combo in ALL:
        assert grade_from_svm(*combo, mode="v22") == V22[combo]
    # 설정 경로의 폴백은 _settings_formula_mode 가 담당한다(빈 값·오타 모두 v22).
    from koipa.modules.m3_labeling import rule_engine as re_mod
    assert re_mod._settings_formula_mode() in re_mod._VALID_FORMULA_MODES
