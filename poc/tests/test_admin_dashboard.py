"""[운영] /admin/dashboard — 분산된 운영 신호를 한 화면용 JSON으로 종합.

각 섹션(보류큐·locked readiness·active-learning·kill-gate·드리프트)은 독립 best-effort —
한 소스가 죽어도 500이 아니라 그 섹션만 비고 degraded에 표기. DB 없는 환경에서도 200 구조 반환.
"""

from __future__ import annotations

import koipa.api.admin as admin
from koipa.api.admin import DashboardResponse, _drift_snapshot, _section, dashboard


def test_dashboard_returns_full_structure_without_db():
    resp = dashboard()
    assert isinstance(resp, DashboardResponse)
    assert resp.generated_at and "T" in resp.generated_at  # ISO8601
    for sec in (
        resp.escalation_held,
        resp.locked_readiness,
        resp.active_learning,
        resp.kill_gate,
        resp.drift,
    ):
        assert isinstance(sec, dict)
    assert isinstance(resp.degraded, list)


def test_section_best_effort_captures_failure():
    degraded: list[str] = []

    def _boom():
        raise RuntimeError("x")

    out = _section("boom", _boom, degraded)
    assert out == {}
    assert "boom" in degraded


def test_section_passthrough_on_success():
    degraded: list[str] = []
    out = _section("ok", lambda: {"a": 1}, degraded)
    assert out == {"a": 1}
    assert degraded == []


def test_dashboard_section_failure_isolated(monkeypatch):
    # 한 소스가 던져도 전체는 200 구조 + degraded에 그 섹션만 표기.
    import koipa.services.confirm_service as cs

    def _boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(cs, "count_needs_second_review_by_grade", _boom)
    resp = dashboard()
    assert "escalation_held" in resp.degraded
    assert resp.escalation_held == {}
    # 다른 섹션은 정상 로드(드리프트 스냅샷은 항상 키 존재).
    assert set(resp.drift.keys()) == {"kl_divergence", "cosine_mean", "alert", "sample_size"}


def test_drift_snapshot_shape_and_floats():
    snap = _drift_snapshot()
    assert set(snap.keys()) == {"kl_divergence", "cosine_mean", "alert", "sample_size"}
    assert all(isinstance(v, float) for v in snap.values())


def test_dashboard_route_registered():
    paths = {r.path for r in admin.router.routes}
    assert "/admin/dashboard" in paths
