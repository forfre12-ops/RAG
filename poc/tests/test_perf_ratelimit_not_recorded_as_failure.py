"""레이트리밋(429)으로 못 잰 값을 '미달'로 적지 않는다.

왜 이 시험이 있는가(2026-09-12). 211 실측(2026-09-11)에서 시나리오를 간격 없이 붙여 돌리자
분류 API 의 레이트리밋(60/min)에 걸려 요청이 429 로 반환됐다. 그런데 하니스는

  * S9.1 변형 일관성 → 표본 0 인데 `0.0` 을 기록해 "일관성 0%" 라는 **미달**로,
  * S16.4 정상 키 통과 → 429 에도 `False` 를 기록해 "정상 키가 막혔다" 는 **미달**로

보고했다. 둘 다 시스템 결함이 아니라 **측정 실패**다. 못 잰 것은 적지 않는다 — 기록이 없으면
하니스가 그 KPI 를 `SKIP` / `no measurement for key '<키>'` 로 집계한다
(harness.ScenarioRunner._compute_kpis). 대신 429 로 빠진 건수를 증거 키로 남겨, 보고서를
읽는 쪽이 콘솔 로그 없이도 SKIP 사유를 안다.

요청 간격은 `PSH_PACE_SEC`(harness.ScenarioRunner.run)로 준다.
"""

from __future__ import annotations

from typing import Any

import pytest

from koipa.perf import scenarios
from koipa.perf.harness import AvailableResources, ScenarioContext


class _Resp:
    def __init__(self, status_code: int, body: dict[str, Any] | None = None) -> None:
        self.status_code = status_code
        self._body = body or {}

    def json(self) -> dict[str, Any]:
        return self._body


class _FakeClient:
    """TestClient 자리에 끼우는 가짜 — post 한 번에 응답 하나를 돌려준다."""

    def __init__(self, responder) -> None:
        self._responder = responder
        self.calls = 0

    def __enter__(self) -> "_FakeClient":
        return self

    def __exit__(self, *exc: object) -> bool:
        return False

    def post(self, url: str, headers: dict | None = None, json: dict | None = None) -> _Resp:
        self.calls += 1
        return self._responder(headers or {}, json or {})


def _install(monkeypatch: pytest.MonkeyPatch, responder) -> None:
    monkeypatch.setattr(scenarios, "_client_factory", lambda: (lambda: _FakeClient(responder)))


def _ctx(scenario_id: str) -> ScenarioContext:
    return ScenarioContext(scenario_id, AvailableResources(), "core")


def _always_429(_headers: dict, _json: dict) -> _Resp:
    return _Resp(429, {"detail": "rate limit exceeded"})


# ----------------------------------------------------------------
# 429 일 때 — 미달로 적지 않고, 사유를 남긴다
# ----------------------------------------------------------------


def test_s9_does_not_record_zero_consistency_when_all_requests_are_ratelimited(monkeypatch):
    _install(monkeypatch, _always_429)
    ctx = _ctx("S9")

    scenarios.s9_adversarial(ctx)

    assert "s9_1" not in ctx.measurements, "못 잰 일관성을 0.0 으로 적으면 미달로 보고된다"
    # 10 케이스 × 3 변형 = 30 요청이 전부 429 로 빠졌다.
    assert ctx.measurements["s9_1_dropped"] == [30.0]
    assert ctx.measurements["s9_1_ratelimited"] == [30.0]


def test_s16_does_not_record_false_when_ratelimited(monkeypatch):
    _install(monkeypatch, _always_429)
    ctx = _ctx("S16")

    scenarios.s16_auth_rejection(ctx)

    for key in ("s16_1", "s16_2", "s16_4"):
        assert key not in ctx.measurements, f"{key}: 429 는 인증 판정이 아니다"
    # 잘못된 키 1 + 헤더 누락 1 + 정상 키 5 = 7 (p95 용 7회는 판정에 안 쓰여 세지 않는다)
    assert ctx.measurements["s16_ratelimited"] == [7.0]


def test_s10_records_dropped_count_as_evidence(monkeypatch):
    _install(monkeypatch, _always_429)
    ctx = _ctx("S10")

    scenarios.s10_evidence_fidelity(ctx)

    for key in ("s10_1", "s10_2", "s10_3"):
        assert key not in ctx.measurements
    dropped = ctx.measurements["s10_dropped"][0]
    assert dropped > 0
    assert ctx.measurements["s10_ratelimited"] == [dropped]


# ----------------------------------------------------------------
# 정상 응답일 때 — 가드가 원래 측정을 막지 않는다
# ----------------------------------------------------------------


def test_s9_still_records_consistency_on_healthy_responses(monkeypatch):
    _install(
        monkeypatch,
        lambda _h, _j: _Resp(200, {"label": "TS", "confidence": 0.9, "status": "auto_confirmed"}),
    )
    ctx = _ctx("S9")

    scenarios.s9_adversarial(ctx)

    assert ctx.measurements["s9_1"] == [1.0]
    assert "s9_1_dropped" not in ctx.measurements
    assert "s9_1_ratelimited" not in ctx.measurements


def test_s16_still_records_auth_verdicts_on_healthy_responses(monkeypatch):
    good = scenarios._api_key()

    def responder(headers: dict, _json: dict) -> _Resp:
        key = headers.get("X-API-Key")
        if key != good:          # 누락·오류 둘 다 401
            return _Resp(401, {"detail": "unauthorized"})
        return _Resp(200, {"label": "S3"})

    _install(monkeypatch, responder)
    ctx = _ctx("S16")

    scenarios.s16_auth_rejection(ctx)

    assert ctx.measurements["s16_1"] == [True]
    assert ctx.measurements["s16_2"] == [True]
    assert ctx.measurements["s16_4"] == [True] * 5
    assert "s16_ratelimited" not in ctx.measurements


# ----------------------------------------------------------------
# 증거 키가 KPI 판정줄을 만들면 안 된다
# ----------------------------------------------------------------


def test_evidence_keys_are_not_kpi_keys():
    """증거 키가 KPI id 와 겹치면 없던 판정 줄이 하나 생긴다."""
    from koipa.perf.kpis import KPIS

    kpi_keys = {k.id.lower().replace(".", "_") for k in KPIS}
    evidence_keys = {
        "s9_1_dropped", "s9_1_ratelimited",
        "s10_dropped", "s10_ratelimited",
        "s16_ratelimited",
    }
    assert not (evidence_keys & kpi_keys), sorted(evidence_keys & kpi_keys)
