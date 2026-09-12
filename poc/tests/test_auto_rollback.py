"""자동 롤백 — C-ver 고도화 (evaluate_rollback_need + auto_rollback_tick).

경계 모킹으로 DB 없이 결정 로직과 tick 배선을 검증. 기본(auto_rollback_enabled=False)은
판정만 하고 롤백 안 함(동작 보존). 라이브 fnr_high가 baseline+tol 초과 시에만 should_rollback.
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

from sqlalchemy.exc import OperationalError

import koipa.services.training_service as ts


@contextmanager
def _cm(db):
    yield db


def _patch(monkeypatch, *, active, live):
    """session_scope/TrainingRepo/compute_metrics_from_db 모킹."""
    monkeypatch.setattr(ts, "session_scope", lambda: _cm(object()))
    monkeypatch.setattr(ts, "TrainingRepo", lambda db: SimpleNamespace(get_active=lambda: active))
    import koipa.modules.m6_evaluation.metrics as m
    monkeypatch.setattr(m, "compute_metrics_from_db", lambda label: live)


def _mv(label="v-x", fnr_high=0.05):
    return SimpleNamespace(version_label=label, metrics=({"fnr_high": fnr_high} if fnr_high is not None else {}))


def _live(samples, ts_fnr=0.0, s1_fnr=0.0):
    return SimpleNamespace(
        sample_count=samples,
        fnr_underclass_by_grade={"TS": ts_fnr, "S1": s1_fnr},
        fnr_underclass_overall=(ts_fnr + s1_fnr) / 2,
    )


# ── evaluate_rollback_need ───────────────────────────────────────────────────


def test_no_active_model(monkeypatch):
    _patch(monkeypatch, active=None, live=None)
    d = ts.evaluate_rollback_need()
    assert d["should_rollback"] is False and d["reason"] == "no_active_model"


class _RaiseCM:
    def __enter__(self):
        raise OperationalError("x", {}, Exception("down"))

    def __exit__(self, *a):
        return False


def test_db_unavailable(monkeypatch):
    monkeypatch.setattr(ts, "session_scope", lambda: _RaiseCM())
    d = ts.evaluate_rollback_need()
    assert d["should_rollback"] is False and d["reason"].startswith("db_unavailable")


def test_no_baseline_fnr(monkeypatch):
    _patch(monkeypatch, active=_mv(fnr_high=None), live=_live(100, 0.5, 0.5))
    d = ts.evaluate_rollback_need()
    assert d["should_rollback"] is False and d["reason"] == "no_baseline_fnr_high"


def test_insufficient_live_data(monkeypatch):
    _patch(monkeypatch, active=_mv(fnr_high=0.05), live=_live(5, 0.9, 0.9))
    d = ts.evaluate_rollback_need(min_samples=20)
    assert d["should_rollback"] is False and d["reason"] == "insufficient_live_data"


def test_regression_triggers_rollback(monkeypatch):
    # baseline 0.05, live 고등급 미탐 0.30(=avg of 0.3/0.3), tol 0.10 → 0.30 > 0.15 → 회귀
    _patch(monkeypatch, active=_mv(fnr_high=0.05), live=_live(50, 0.30, 0.30))
    d = ts.evaluate_rollback_need(min_samples=20, fnr_high_tolerance=0.10)
    assert d["should_rollback"] is True
    assert d["live_fnr_high"] == 0.3 and d["baseline_fnr_high"] == 0.05


def test_within_tolerance_no_rollback(monkeypatch):
    _patch(monkeypatch, active=_mv(fnr_high=0.05), live=_live(50, 0.06, 0.06))
    d = ts.evaluate_rollback_need(min_samples=20, fnr_high_tolerance=0.10)
    assert d["should_rollback"] is False and d["reason"] == "within tolerance"


# ── auto_rollback_tick 배선 ──────────────────────────────────────────────────


def test_tick_disabled_does_not_rollback(monkeypatch):
    from koipa.config import settings
    from koipa.workers.tasks import auto_rollback_tick
    monkeypatch.setattr(settings, "auto_rollback_enabled", False)
    monkeypatch.setattr(ts, "evaluate_rollback_need",
                        lambda: {"should_rollback": True, "live_fnr_high": 0.3})
    called = {"n": 0}
    monkeypatch.setattr(ts, "rollback_active_model", lambda r: called.__setitem__("n", called["n"] + 1))
    out = auto_rollback_tick()
    assert out["auto_rollback_enabled"] is False
    assert "rollback" not in out
    assert called["n"] == 0


def test_tick_enabled_rolls_back(monkeypatch):
    from koipa.config import settings
    from koipa.workers.tasks import auto_rollback_tick
    monkeypatch.setattr(settings, "auto_rollback_enabled", True)
    monkeypatch.setattr(ts, "evaluate_rollback_need",
                        lambda: {"should_rollback": True, "live_fnr_high": 0.3,
                                 "baseline_fnr_high": 0.05, "tolerance": 0.1})
    monkeypatch.setattr(ts, "rollback_active_model",
                        lambda r: {"rolled_back": True, "reason": r})
    out = auto_rollback_tick()
    assert out["auto_rollback_enabled"] is True
    assert out["rollback"]["rolled_back"] is True
