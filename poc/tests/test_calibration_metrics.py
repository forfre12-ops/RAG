"""캘리브레이션 측정(ECE·Brier·reliability) — 값이 뜻대로 나오는가.

왜 이 시험이 있는가(2026-09-06). `koipa.modules.m6_evaluation.calibration` 은
**커버리지 0.0%** 였다. 2026-09-05 에 내가 `scripts/render_eval_report.py` 에 연결해
평가 리포트가 이 값을 싣게 했는데, 그 모듈을 실행하는 시험이 하나도 없었다.

왜 중요한가 — 이 시스템에서 **미보정 서빙은 OOD 과신 → 고등급 무음 미탐**으로 이어진다
(memory: model-serving-needs-calibration). ECE 는 "confidence 0.9 라고 한 예측이 실제로
90% 맞는가"를 재는 값이고, 자동확정 게이트가 직접 그 위에 선다. 그 계산이 틀리면
리포트가 "보정 잘 돼 있다"고 말하면서 실제로는 아닐 수 있다.

⚠ 이 시험은 **값의 뜻**을 고정한다 — 구현을 그대로 옮겨 적지 않는다. 완벽 보정은 0,
  전부 틀린 과신은 1 에 가깝다는 성질로 확인한다.
"""
from __future__ import annotations

import math

from koipa.modules.m6_evaluation.calibration import (
    brier_score,
    compute_calibration_from_arrays,
    expected_calibration_error,
    reliability_bins,
)


# ── ECE — 완벽 보정은 0, 과신은 크다 ────────────────────────────────────────
def test_ece_is_zero_when_perfectly_calibrated():
    """confidence 가 실제 정답률과 같으면 ECE = 0.

    0.95 라고 말한 20건 중 19건이 맞고, 0.55 라고 말한 20건 중 11건이 맞는 경우다.
    """
    conf = [0.95] * 20 + [0.55] * 20
    correct = [True] * 19 + [False] * 1 + [True] * 11 + [False] * 9
    assert expected_calibration_error(conf, correct) == 0.0


def test_ece_is_large_when_overconfident():
    """전부 0.99 라고 하고 절반이 틀리면 ECE 가 0.5 근처다 — 이 시스템이 가장 두려워하는 모양."""
    conf = [0.99] * 100
    correct = [True] * 50 + [False] * 50
    ece = expected_calibration_error(conf, correct)
    assert 0.45 < ece < 0.55, ece


def test_ece_is_large_when_underconfident():
    """반대 방향도 잡는다 — 0.1 이라 해 놓고 전부 맞히면 그것도 미보정이다."""
    ece = expected_calibration_error([0.1] * 50, [True] * 50)
    assert ece > 0.8, ece


def test_ece_of_empty_input_is_zero_not_error():
    """표본이 없으면 0 을 낸다 — 리포트가 죽지 않는다(다만 sample_count 로 구분 가능해야 한다)."""
    assert expected_calibration_error([], []) == 0.0
    assert compute_calibration_from_arrays([], []).sample_count == 0


# ── Brier — 정답에 가까울수록 낮다 ─────────────────────────────────────────
def test_brier_is_zero_for_certain_and_right():
    assert brier_score([1.0] * 10, [True] * 10) == 0.0


def test_brier_is_one_for_certain_and_wrong():
    """확신했는데 전부 틀리면 최댓값 1 — 무음 미탐이 바로 이 자리다."""
    assert brier_score([1.0] * 10, [False] * 10) == 1.0


def test_brier_penalises_confidence_quadratically():
    """0.5 로 반반 찍으면 0.25 — 선형이 아니라 제곱 벌점이다."""
    assert math.isclose(brier_score([0.5] * 8, [True] * 4 + [False] * 4), 0.25, abs_tol=1e-6)


# ── reliability bins — 구간이 빠지지 않는가 ─────────────────────────────────
def test_bins_cover_the_unit_interval_and_count_every_sample():
    conf = [0.05, 0.15, 0.35, 0.55, 0.75, 0.95, 1.0]
    bins = reliability_bins(conf, [True] * len(conf), n_bins=10)
    assert len(bins) == 10
    assert sum(b["count"] for b in bins) == len(conf), "표본이 어느 구간에도 안 들어갔다"
    # 마지막 구간은 1.0 을 포함해야 한다 — 경계에서 표본이 사라지면 ECE 가 틀어진다.
    assert bins[-1]["count"] >= 2, bins[-1]


def test_empty_bin_reports_zero_not_missing():
    """빈 구간도 자리를 남긴다 — 그래야 화면이 구간을 건너뛰지 않는다."""
    bins = reliability_bins([0.95] * 5, [True] * 5, n_bins=10)
    assert len(bins) == 10
    assert bins[0]["count"] == 0 and bins[0]["gap"] == 0.0


def test_bin_gap_is_confidence_minus_accuracy():
    """gap 이 |평균신뢰도 − 정확도| 인지 — ECE 가 이 값을 가중 평균한다."""
    bins = reliability_bins([0.95] * 4, [True, True, False, False], n_bins=10)
    top = [b for b in bins if b["count"]][0]
    assert math.isclose(top["avg_confidence"], 0.95, abs_tol=1e-6)
    assert math.isclose(top["accuracy"], 0.5, abs_tol=1e-6)
    assert math.isclose(top["gap"], 0.45, abs_tol=1e-6)


# ── 리포트가 싣는 형태 ──────────────────────────────────────────────────────
def test_result_carries_what_the_report_prints():
    """render_eval_report 가 읽는 칸이 다 있는가 — 없으면 리포트가 조용히 빈칸이 된다."""
    res = compute_calibration_from_arrays(
        [0.9] * 10 + [0.6] * 10, [True] * 9 + [False] + [True] * 6 + [False] * 4,
        model_version="v-test",
    )
    d = res.to_dict()
    for key in ("model_version", "sample_count", "ece", "brier", "bins"):
        assert key in d, key
    assert d["model_version"] == "v-test"
    assert d["sample_count"] == 20
    assert len(d["bins"]) == 10
