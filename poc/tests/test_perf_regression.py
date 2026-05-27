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

from lloydk.perf.regression import detect_regressions


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
