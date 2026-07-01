"""Kill-gate 순수 결정함수 테스트 (DB 불요) — 죽음의 나선/품질붕괴 모니터(경보만).

발동 조건: ① TS/S1 미탐(고등급 underclass) ≥ floor ② 검수 과부하(일교정 ≥ capacity×배수)
③ overturn율 ≥ max. floor/capacity/max=0이면 해당 조건 비활성.
"""
from __future__ import annotations

from prometheus_client import generate_latest

from lloydk.api.prom_metrics import KILL_GATE_TRIPPED, registry
from lloydk.modules.m6_evaluation.kill_gate import evaluate_kill_gate


def test_not_tripped_when_clean():
    r = evaluate_kill_gate(
        high_grade_miss_count=0, daily_correction_count=10,
        overturn_rate=0.1, daily_capacity=100,
    )
    assert r.tripped is False
    assert r.reasons == ()


def test_high_grade_miss_trips():
    # TS/S1 미탐 1건이면 발동(절대 floor — 이 사업 최악 오류)
    r = evaluate_kill_gate(
        high_grade_miss_count=1, daily_correction_count=0,
        overturn_rate=0.0, daily_capacity=0,
    )
    assert r.tripped is True
    assert any("high_grade_miss" in x for x in r.reasons)


def test_high_grade_miss_floor_zero_disables():
    r = evaluate_kill_gate(
        high_grade_miss_count=5, daily_correction_count=0,
        overturn_rate=0.0, daily_capacity=0, high_grade_miss_floor=0,
    )
    assert r.tripped is False


def test_fatigue_trips_over_capacity():
    # 일 교정 160 ≥ capacity 100 × 1.5 = 150 → 과부하
    r = evaluate_kill_gate(
        high_grade_miss_count=0, daily_correction_count=160,
        overturn_rate=0.0, daily_capacity=100, fatigue_multiplier=1.5,
    )
    assert r.tripped is True
    assert any("fatigue" in x for x in r.reasons)


def test_fatigue_disabled_when_capacity_zero():
    # capacity 0 = fatigue 비활성 (운영이 처리능력 설정 전 무발동)
    r = evaluate_kill_gate(
        high_grade_miss_count=0, daily_correction_count=9999,
        overturn_rate=0.0, daily_capacity=0,
    )
    assert r.tripped is False


def test_overturn_rate_trips():
    r = evaluate_kill_gate(
        high_grade_miss_count=0, daily_correction_count=0,
        overturn_rate=0.40, daily_capacity=0, overturn_rate_max=0.30,
    )
    assert r.tripped is True
    assert any("overturn" in x for x in r.reasons)


def test_multiple_reasons_accumulate():
    r = evaluate_kill_gate(
        high_grade_miss_count=2, daily_correction_count=200,
        overturn_rate=0.5, daily_capacity=100,
    )
    assert r.tripped is True
    assert len(r.reasons) == 3  # 세 조건 모두


def test_kill_gate_gauge_defined_and_exposed():
    KILL_GATE_TRIPPED.set(0)
    expo = generate_latest(registry).decode()
    assert "lloydk_kill_gate_tripped" in expo
