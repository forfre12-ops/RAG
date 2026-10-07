"""[C3] 온도 스케일링 코어 — m6_evaluation.temperature.

trainer 자동연결과 calibrate_classifier.py 가 공유하는 순수 로직. GPU·DB 불요.
"""
from __future__ import annotations

import math

from koipa.modules.m6_evaluation.temperature import (
    expected_calibration_error,
    find_best_temperature,
    fit_temperature_report,
    softmax,
)


def test_softmax_sums_to_one_and_temperature_smooths():
    p1 = softmax([4.0, 0.0, 0.0, 0.0], temperature=1.0)
    assert abs(sum(p1) - 1.0) < 1e-9
    p_hi = softmax([4.0, 0.0, 0.0, 0.0], temperature=3.0)
    # 높은 T는 분포를 부드럽게 — top 확률이 낮아진다.
    assert max(p_hi) < max(p1)


# over-confident: 8건은 class0 정답, 2건은 logit이 class0를 강하게 가리키나 실제는 class1.
_OVERCONF_LOGITS = [[4.0, 0.0, 0.0, 0.0]] * 10
_OVERCONF_LABELS = [0] * 8 + [1] * 2


def test_over_confident_model_calibrates_to_T_above_one():
    T = find_best_temperature(_OVERCONF_LOGITS, _OVERCONF_LABELS)
    assert T > 1.0   # 과신 → 분포를 부드럽게(T>1)해 NLL 최소화 (MEMORY: 서빙 T≈3)


def test_calibration_reduces_ece():
    T = find_best_temperature(_OVERCONF_LOGITS, _OVERCONF_LABELS)
    ece_before = expected_calibration_error(_OVERCONF_LOGITS, _OVERCONF_LABELS, 1.0)
    ece_after = expected_calibration_error(_OVERCONF_LOGITS, _OVERCONF_LABELS, T)
    assert ece_after <= ece_before


def test_fit_temperature_report_schema():
    rep = fit_temperature_report(_OVERCONF_LOGITS, _OVERCONF_LABELS)
    assert set(rep) >= {"temperature", "ece_before", "ece_after", "nll_before", "nll_after", "n_samples"}
    assert rep["n_samples"] == 10
    assert rep["temperature"] > 1.0
    assert rep["nll_after"] <= rep["nll_before"]   # NLL 최소화가 목적함수
    assert not math.isnan(rep["ece_after"])
