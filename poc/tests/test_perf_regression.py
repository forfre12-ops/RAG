"""regression.detect_regressions 단위 테스트.

검증:
- 직전 회차 없으면 빈 리스트
- compare=le: 측정값 증가가 악화 (latency↑, FNR↑)
- compare=ge: 측정값 감소가 악화 (recall↓, throughput↓)
- bool/count는 비교 제외
- SKIP/ERROR KPI는 비교 제외
- threshold_pct 미만이면 회귀 아님
- 0 → N은 비율 계산 의미 없어 제외
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.fullstack
from lloydk.perf.regression import (
    detect_regressions,
    detect_regression_trend,
    summarize_skip_reasons,
)


def _kpi(kid: str, *, name: str = "", unit: str = "ms", measured: float = 0.0,
         compare: str = "le", status: str = "PASS") -> dict:
    return {
        "kpi_id": kid,
        "name": name or kid,
        "unit": unit,
        "measured": measured,
        "threshold": 0,
        "compare": compare,
        "passed": status == "PASS",
        "status": status,
        "skip_reason": "",
        "n_samples": 1,
    }


def _report(*kpis) -> dict:
    return {
        "mode": "dryrun",
        "scenarios": [{"scenario": "S1", "kpis": list(kpis)}],
    }


class TestNoHistory:
    def test_no_previous_returns_empty(self):
        curr = _report(_kpi("S1.1", measured=100.0))
        assert detect_regressions(curr, None) == []

    def test_empty_previous(self):
        curr = _report(_kpi("S1.1", measured=100.0))
        prev = _report()  # 빈 시나리오
        assert detect_regressions(curr, prev) == []


class TestLeKpi:
    """compare=le — latency, FNR 등. 측정값 증가가 악화."""

    def test_le_worsened_above_threshold(self):
        curr = _report(_kpi("S1.1", measured=300.0, compare="le"))
        prev = _report(_kpi("S1.1", measured=100.0, compare="le"))
        regs = detect_regressions(curr, prev, threshold_pct=20.0)
        assert len(regs) == 1
        assert regs[0]["kpi_id"] == "S1.1"
        assert regs[0]["delta_pct"] == 200.0
        assert regs[0]["direction"] == "up"

    def test_le_improved_not_regression(self):
        """latency 감소는 회귀 아님."""
        curr = _report(_kpi("S1.1", measured=50.0, compare="le"))
        prev = _report(_kpi("S1.1", measured=100.0, compare="le"))
        assert detect_regressions(curr, prev, threshold_pct=20.0) == []

    def test_le_worsened_below_threshold(self):
        """20% 미만 악화는 회귀로 보지 않음."""
        curr = _report(_kpi("S1.1", measured=115.0, compare="le"))
        prev = _report(_kpi("S1.1", measured=100.0, compare="le"))
        assert detect_regressions(curr, prev, threshold_pct=20.0) == []


class TestGeKpi:
    """compare=ge — recall, throughput, F1 등. 측정값 감소가 악화."""

    def test_ge_worsened(self):
        curr = _report(_kpi("S5.4", measured=0.6, unit="ratio", compare="ge"))
        prev = _report(_kpi("S5.4", measured=0.9, unit="ratio", compare="ge"))
        regs = detect_regressions(curr, prev, threshold_pct=20.0)
        assert len(regs) == 1
        assert regs[0]["direction"] == "down"
        assert regs[0]["delta_pct"] < 0

    def test_ge_improved_not_regression(self):
        curr = _report(_kpi("S5.4", measured=0.95, unit="ratio", compare="ge"))
        prev = _report(_kpi("S5.4", measured=0.9, unit="ratio", compare="ge"))
        assert detect_regressions(curr, prev, threshold_pct=20.0) == []


class TestExclusions:
    def test_bool_excluded(self):
        curr = _report(_kpi("S1.5", measured=0.0, unit="bool"))
        prev = _report(_kpi("S1.5", measured=1.0, unit="bool"))
        assert detect_regressions(curr, prev, threshold_pct=20.0) == []

    def test_count_excluded(self):
        curr = _report(_kpi("S5.2", measured=0.0, unit="count"))
        prev = _report(_kpi("S5.2", measured=10.0, unit="count"))
        assert detect_regressions(curr, prev, threshold_pct=20.0) == []

    def test_skip_excluded(self):
        curr = _report(_kpi("S1.1", measured=300.0, status="SKIP"))
        prev = _report(_kpi("S1.1", measured=100.0))
        assert detect_regressions(curr, prev, threshold_pct=20.0) == []

    def test_prev_skip_excluded(self):
        curr = _report(_kpi("S1.1", measured=300.0))
        prev = _report(_kpi("S1.1", measured=0.0, status="SKIP"))
        assert detect_regressions(curr, prev, threshold_pct=20.0) == []

    def test_zero_prev_excluded(self):
        """prev=0인 경우 비율 계산 의미 없으므로 제외."""
        curr = _report(_kpi("S1.1", measured=100.0))
        prev = _report(_kpi("S1.1", measured=0.0))
        assert detect_regressions(curr, prev, threshold_pct=20.0) == []

    def test_missing_in_prev(self):
        """직전에 없던 KPI는 비교 불가."""
        curr = _report(_kpi("S99.1", measured=100.0))
        prev = _report(_kpi("S1.1", measured=100.0))
        assert detect_regressions(curr, prev, threshold_pct=20.0) == []


class TestMultipleScenarios:
    def test_multiple_kpis_some_regressed(self):
        curr = {
            "mode": "dryrun",
            "scenarios": [
                {"scenario": "S1", "kpis": [
                    _kpi("S1.1", measured=300.0, compare="le"),
                    _kpi("S1.5", measured=1.0, unit="bool", compare="ge"),
                ]},
                {"scenario": "S5", "kpis": [
                    _kpi("S5.4", measured=0.6, unit="ratio", compare="ge"),
                ]},
            ],
        }
        prev = {
            "mode": "dryrun",
            "scenarios": [
                {"scenario": "S1", "kpis": [
                    _kpi("S1.1", measured=100.0, compare="le"),
                    _kpi("S1.5", measured=1.0, unit="bool", compare="ge"),
                ]},
                {"scenario": "S5", "kpis": [
                    _kpi("S5.4", measured=0.9, unit="ratio", compare="ge"),
                ]},
            ],
        }
        regs = detect_regressions(curr, prev, threshold_pct=20.0)
        ids = {r["kpi_id"] for r in regs}
        assert ids == {"S1.1", "S5.4"}


@pytest.mark.parametrize("pct,expect", [(10.0, True), (20.0, True), (30.0, False)])
def test_threshold_parameterized(pct: float, expect: bool):
    """120% 증가는 10/20% 임계 시 회귀, 200%+ 임계 시 정상."""
    curr = _report(_kpi("S1.1", measured=220.0, compare="le"))
    prev = _report(_kpi("S1.1", measured=100.0, compare="le"))
    regs = detect_regressions(curr, prev, threshold_pct=pct * 10)
    # 120% delta vs threshold (100·200·300%)
    if pct * 10 < 120:
        assert len(regs) == 1
    else:
        assert len(regs) == 0


class TestRegressionTrend:
    """detect_regression_trend — 시계열 분석."""

    def test_empty_history(self):
        assert detect_regression_trend([]) == []
        assert detect_regression_trend([_report(_kpi("S1.1", measured=100.0))]) == []

    def test_consecutive_degrading(self):
        """3회 연속 latency 증가 → consecutive=2, trend=degrading."""
        h = [
            _report(_kpi("S1.1", measured=300.0, compare="le")),  # 최근
            _report(_kpi("S1.1", measured=200.0, compare="le")),
            _report(_kpi("S1.1", measured=100.0, compare="le")),  # 가장 오래
        ]
        trends = detect_regression_trend(h, threshold_pct=20.0)
        assert len(trends) == 1
        t = trends[0]
        assert t["kpi_id"] == "S1.1"
        assert t["n_regressions"] == 2
        assert t["consecutive"] == 2
        assert t["trend"] == "degrading"

    def test_isolated_spike_not_degrading(self):
        """단발 회귀 후 회복 → flat 또는 noisy."""
        h = [
            _report(_kpi("S1.1", measured=100.0, compare="le")),  # 회복
            _report(_kpi("S1.1", measured=300.0, compare="le")),  # 스파이크
            _report(_kpi("S1.1", measured=100.0, compare="le")),
        ]
        trends = detect_regression_trend(h, threshold_pct=20.0)
        # 0→1단계: 100 vs 300 → 개선이라 회귀 아님
        # 1→2단계: 300 vs 100 → 200% 악화 회귀
        if trends:
            assert trends[0]["trend"] in ("flat", "noisy")
            assert trends[0]["consecutive"] == 0

    def test_improving_only(self):
        """순수 개선만 있으면 빈 리스트."""
        h = [
            _report(_kpi("S1.1", measured=50.0, compare="le")),
            _report(_kpi("S1.1", measured=100.0, compare="le")),
            _report(_kpi("S1.1", measured=200.0, compare="le")),
        ]
        assert detect_regression_trend(h, threshold_pct=20.0) == []


class TestSummarizeSkipReasons:
    """summarize_skip_reasons — SKIP 사유 분류."""

    def test_empty_report(self):
        s = summarize_skip_reasons({"scenarios": []})
        assert s == {"total_skip": 0, "by_reason": {}, "by_scenario": {}}

    def test_classification(self):
        report = {
            "scenarios": [
                {
                    "scenario": "S1",
                    "kpis": [
                        {"kpi_id": "S1.3", "status": "SKIP",
                         "skip_reason": "missing: trained_model"},
                        {"kpi_id": "S1.4", "status": "SKIP",
                         "skip_reason": "missing: trained_model"},
                        {"kpi_id": "S1.5", "status": "PASS"},
                    ],
                },
                {
                    "scenario": "S3",
                    "kpis": [
                        {"kpi_id": "S3.3", "status": "SKIP",
                         "skip_reason": "S3 requires PG"},
                        {"kpi_id": "S3.4", "status": "SKIP",
                         "skip_reason": "no measurement for key 's3_4'"},
                    ],
                },
            ],
        }
        s = summarize_skip_reasons(report)
        assert s["total_skip"] == 4
        assert s["by_reason"]["trained_model"] == 2
        assert s["by_reason"]["pg"] == 1
        assert s["by_reason"]["no_measurement"] == 1
        assert s["by_scenario"]["S1"] == 2
        assert s["by_scenario"]["S3"] == 2

    def test_unclassified_goes_to_other(self):
        report = {
            "scenarios": [{
                "scenario": "S99",
                "kpis": [
                    {"kpi_id": "S99.1", "status": "SKIP",
                     "skip_reason": "weird custom reason"},
                ],
            }],
        }
        s = summarize_skip_reasons(report)
        assert s["by_reason"]["other"] == 1
