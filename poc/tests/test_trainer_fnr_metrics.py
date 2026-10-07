"""미탐(FNR) 계산의 **뜻**을 고정한다 — 이 사업의 1차 지표다.

왜 이 시험이 있는가(2026-09-06). `trainer.py` 는 커버리지 **44.6%** 였고, FNR 을 만드는
세 함수(_compute_fnr · _compute_fnr_high · _compute_over_class_rate)가 그 안에 있었다.
이 값이 틀리면 **모든 학습 리포트·승격 게이트·제출 수치가 함께 틀린다.**

RFP 는 정확도 수치 목표를 두지 않는다(PER-002 는 속도·자원). 이 사업이 스스로 정한
1차 목표가 **미탐 최소화**이고, 그 목표를 재는 것이 바로 이 함수들이다.

⚠ 구현을 옮겨 적지 않는다. **의미**를 고정한다 — 특히 세 함수가 서로 다른 것을 센다는
  사실을. 그 차이를 모르면 리포트를 잘못 읽는다.

    _LABEL_LIST = [TS, S1, S2, S3]  →  id 가 **작을수록 심각**하다.
"""
from __future__ import annotations

import numpy as np

from koipa.modules.m4_training.trainer import (
    _ID2LABEL,
    _LABEL_LIST,
    _compute_fnr,
    _compute_fnr_high,
    _compute_over_class_rate,
)

TS, S1, S2, S3 = 0, 1, 2, 3
HIGH = [TS, S1]


def _cm(pairs: dict[tuple[int, int], int]) -> np.ndarray:
    """{(정답, 예측): 건수} → 혼동행렬."""
    m = np.zeros((4, 4), dtype=int)
    for (t, p), n in pairs.items():
        m[t, p] = n
    return m


def test_label_order_is_severity_order():
    """id 가 작을수록 심각 — 아래 시험들이 전부 이 전제 위에 선다."""
    assert _LABEL_LIST == ["TS", "S1", "S2", "S3"]
    assert _ID2LABEL[0] == "TS" and _ID2LABEL[3] == "S3"


# ── _compute_fnr — 등급별 '틀린 비율'(방향 무관) ────────────────────────────
def test_fnr_is_zero_on_a_perfect_diagonal():
    cm = _cm({(g, g): 10 for g in range(4)})
    overall, by = _compute_fnr(cm)
    assert overall == 0.0
    assert by == {"TS": 0.0, "S1": 0.0, "S2": 0.0, "S3": 0.0}


def test_fnr_counts_any_misclassification_not_only_underclass():
    """⚠ _compute_fnr 은 **방향을 보지 않는다** — 과분류도 센다.

    S3 를 TS 로 본 것(과분류)도 여기서는 S3 의 fnr 로 잡힌다. 미탐만 보려면
    _compute_fnr_high 를 써야 한다. 두 값을 같은 것으로 읽으면 리포트를 잘못 읽는다.
    """
    cm = _cm({(S3, TS): 4, (S3, S3): 6, (TS, TS): 10, (S1, S1): 10, (S2, S2): 10})
    _overall, by = _compute_fnr(cm)
    assert by["S3"] == 0.4, "과분류인데도 S3 의 fnr 로 잡힌다"
    assert by["TS"] == 0.0


def test_fnr_overall_is_weighted_by_row_totals():
    """전체 fnr 은 등급별 평균이 아니라 **표본 수 가중**이다."""
    cm = _cm({(TS, S3): 1, (TS, TS): 1, (S1, S1): 98})
    overall, by = _compute_fnr(cm)
    assert by["TS"] == 0.5
    assert abs(overall - 1 / 100) < 1e-9, "1/100 이어야 한다(등급 평균 0.25 가 아니다)"


def test_fnr_ignores_grades_with_no_samples():
    """표본이 없는 등급은 0 으로 두고 분모에도 안 들어간다 — 0으로 나누지 않는다."""
    cm = _cm({(TS, TS): 5})
    overall, by = _compute_fnr(cm)
    assert overall == 0.0 and by["S2"] == 0.0


# ── _compute_fnr_high — 고등급 '미탐'(방향 있음) ───────────────────────────
def test_fnr_high_counts_only_underclassification():
    """진짜 TS 를 더 낮은 등급으로 본 것만 센다 — 그것이 이 사업이 두려워하는 실패다."""
    cm = _cm({(TS, S3): 3, (TS, TS): 7, (S1, S1): 10})
    assert abs(_compute_fnr_high(cm, HIGH) - 3 / 20) < 1e-9


def test_fnr_high_does_not_count_overclassification():
    """S1 을 TS 로 본 것(더 심각하게 봄)은 미탐이 아니다 — 안전한 방향이다."""
    cm = _cm({(S1, TS): 5, (S1, S1): 5, (TS, TS): 10})
    assert _compute_fnr_high(cm, HIGH) == 0.0


def test_fnr_high_ignores_low_grade_errors():
    """S2·S3 가 틀린 것은 고등급 미탐이 아니다."""
    cm = _cm({(S2, S3): 9, (S2, S2): 1, (TS, TS): 10, (S1, S1): 10})
    assert _compute_fnr_high(cm, HIGH) == 0.0


def test_fnr_high_is_zero_without_high_grade_samples():
    """고등급 표본이 없으면 0 — 나눗셈이 죽지 않는다.

    ⚠ 그 0 은 "미탐이 없다"가 아니라 "잴 대상이 없다"이다. 표본 수와 함께 읽어야 한다.
    """
    assert _compute_fnr_high(_cm({(S3, S3): 10}), HIGH) == 0.0


# ── 게이밍 방어 — 왜 과분류율을 함께 보는가 ────────────────────────────────
def test_predicting_everything_as_ts_games_fnr_high_to_zero():
    """**전부 TS 로 찍는 예측기는 fnr_high 를 0 으로 만든다.**

    그래서 fnr_high 단독으로 모델을 고르면 안 된다 — 코드 주석이 경고하는 그것이고,
    과분류율이 그 짝으로 있는 이유다.
    """
    cm = _cm({(g, TS): 10 for g in range(4)})
    assert _compute_fnr_high(cm, HIGH) == 0.0, "미탐 0 — 완벽해 보인다"
    assert _compute_over_class_rate(cm, HIGH) == 1.0, "그런데 저등급 전량을 고등급으로 올렸다"


def test_over_class_rate_is_zero_when_low_grades_stay_low():
    cm = _cm({(S2, S2): 10, (S3, S3): 10, (TS, TS): 5, (S1, S1): 5})
    assert _compute_over_class_rate(cm, HIGH) == 0.0


def test_over_class_rate_ignores_high_grade_rows():
    """분모는 **저등급 표본**이다 — 고등급이 고등급으로 간 것은 과분류가 아니다."""
    cm = _cm({(TS, TS): 10, (S1, TS): 10, (S2, S2): 8, (S2, TS): 2})
    assert abs(_compute_over_class_rate(cm, HIGH) - 2 / 10) < 1e-9
