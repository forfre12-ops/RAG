"""배포 합격선 게이트 단위 테스트 — A2-② / C-ver.

순수 함수 evaluate_deploy_gate의 fail-SECURE 판정을 검증한다. DB·모델 불요.
영업비밀 안전원칙: 미탐(fnr_high) 악화·degenerate·검증불가는 무조건 배포 차단.
"""

from __future__ import annotations

from lloydk.modules.m6_evaluation.deploy_gate import (
    DeployDecision,
    evaluate_deploy_gate,
)


def _report(fnr_high=0.05, f1_macro=0.85, cm=None):
    d = {"fnr_high": fnr_high, "f1_macro": f1_macro}
    if cm is not None:
        d["confusion_matrix"] = cm
    return d


# ── 최초 배포 (baseline 없음) ────────────────────────────────────────────────


def test_first_deploy_passes_without_baseline():
    dec = evaluate_deploy_gate(_report(fnr_high=0.1, f1_macro=0.7), None)
    assert isinstance(dec, DeployDecision)
    assert dec.passed is True
    assert dec.had_baseline is False


def test_first_deploy_blocked_when_require_baseline():
    dec = evaluate_deploy_gate(_report(), None, require_baseline=True)
    assert dec.passed is False
    assert "baseline_present" in dec.reason


# ── fnr_high 회귀 차단 (미탐 악화) ───────────────────────────────────────────


def test_blocks_fnr_high_regression():
    cand = _report(fnr_high=0.20)   # 미탐 악화
    base = _report(fnr_high=0.05)
    dec = evaluate_deploy_gate(cand, base, fnr_high_tolerance=0.02)
    assert dec.passed is False
    assert "fnr_high_regression" in dec.reason


def test_allows_fnr_high_within_tolerance():
    cand = _report(fnr_high=0.06)   # +0.01 ≤ tol 0.02
    base = _report(fnr_high=0.05)
    dec = evaluate_deploy_gate(cand, base, fnr_high_tolerance=0.02)
    assert dec.passed is True


def test_allows_fnr_high_improvement():
    cand = _report(fnr_high=0.01)   # 개선
    base = _report(fnr_high=0.05)
    dec = evaluate_deploy_gate(cand, base)
    assert dec.passed is True


# ── f1 회귀 차단 ─────────────────────────────────────────────────────────────


def test_blocks_f1_regression():
    cand = _report(fnr_high=0.04, f1_macro=0.60)   # fnr는 좋아졌지만 f1 폭락
    base = _report(fnr_high=0.05, f1_macro=0.85)
    dec = evaluate_deploy_gate(cand, base, f1_drop_tolerance=0.05)
    assert dec.passed is False
    assert "f1_regression" in dec.reason


def test_allows_f1_within_tolerance():
    cand = _report(fnr_high=0.04, f1_macro=0.81)   # -0.04 ≤ tol 0.05
    base = _report(fnr_high=0.05, f1_macro=0.85)
    dec = evaluate_deploy_gate(cand, base, f1_drop_tolerance=0.05)
    assert dec.passed is True


# ── degenerate 가드 ──────────────────────────────────────────────────────────


def test_blocks_degenerate_all_one_class():
    # 4x4 cm: 모든 예측이 0번 열(TS)로 — '전부 TS' degenerate
    cm = [[10, 0, 0, 0], [10, 0, 0, 0], [10, 0, 0, 0], [10, 0, 0, 0]]
    cand = _report(fnr_high=0.0, f1_macro=0.9, cm=cm)   # fnr_high=0으로 게이밍 시도
    dec = evaluate_deploy_gate(cand, None)
    assert dec.passed is False
    assert "degenerate" in dec.reason


def test_non_degenerate_cm_ok():
    cm = [[8, 1, 1, 0], [1, 7, 1, 1], [0, 1, 8, 1], [0, 0, 1, 9]]
    cand = _report(fnr_high=0.05, f1_macro=0.85, cm=cm)
    dec = evaluate_deploy_gate(cand, None)
    assert dec.passed is True


# ── fail-CLOSED: fnr_high 부재 ───────────────────────────────────────────────


def test_blocks_when_fnr_high_missing():
    cand = {"f1_macro": 0.9}   # fnr_high 없음 — 검증 불가
    dec = evaluate_deploy_gate(cand, None)
    assert dec.passed is False
    assert "fnr_high_present" in dec.reason


# ── 객체/딕셔너리 호환 (TrainReport 류) ──────────────────────────────────────


def test_accepts_object_with_to_dict():
    class FakeReport:
        def to_dict(self):
            return {"fnr_high": 0.03, "f1_macro": 0.88}

    dec = evaluate_deploy_gate(FakeReport(), None)
    assert dec.passed is True


def test_accepts_dataclass_like_dict_attr():
    class Bag:
        def __init__(self):
            self.fnr_high = 0.03
            self.f1_macro = 0.88

    dec = evaluate_deploy_gate(Bag(), None)
    assert dec.passed is True


def test_decision_to_dict_serializable():
    dec = evaluate_deploy_gate(_report(), _report())
    d = dec.to_dict()
    assert d["passed"] is True
    assert isinstance(d["checks"], list) and d["checks"]
    assert all({"name", "passed", "detail"} <= set(c) for c in d["checks"])
