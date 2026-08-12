"""[obs] 서빙 파이프라인(m5_inference) 게이트 fail-open 가시화.

FNR-safe override·source-prior cap·metadata-floor 는 예외 시 등급 조정을 건너뛰고(fail-safe)
분류를 계속한다. 무음이면 상향/라우팅 게이트가 조용히 실패해 비밀이 낮은 등급을 유지했는지
안 보인다 — 예외 fail-open 시 SERVING_GATE_FAIL_OPEN_TOTAL{gate} 증가를 고정("게이트ON=가시성ON").
"""
from __future__ import annotations

import pytest

from koipa.api import prom_metrics as pm
from koipa.modules.m5_inference import pipeline as P
from koipa.modules.m5_inference.pipeline import InferencePipeline


def _gate_val(gate) -> float:
    return pm.SERVING_GATE_FAIL_OPEN_TOTAL.labels(gate=gate)._value.get()


def _boom(*_a, **_k):
    raise RuntimeError("gate boom")


@pytest.mark.parametrize("gate", ["fnr_safe_override", "source_prior_cap", "metadata_floor"])
def test_pipeline_record_gate_fail_open_increments(gate):
    before = _gate_val(gate)
    P._record_gate_fail_open(gate)
    assert _gate_val(gate) == before + 1


def test_pipeline_record_gate_fail_open_best_effort(monkeypatch):
    # 메트릭 import 실패해도 예외 전파 없음(분류 경로 무영향).
    import builtins
    real = builtins.__import__

    def _no_metrics(name, *a, **k):
        if name == "koipa.api.prom_metrics":
            raise ImportError("x")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_metrics)
    P._record_gate_fail_open("metadata_floor")  # 예외 없으면 통과


def test_metadata_floor_fail_open_records_and_stays_fail_safe(monkeypatch):
    """실제 콜사이트: metadata-floor 상향 진입 후 _enforce_label_consistency 예외 →
    분류는 계속(fail-safe, 원 등급 유지) + metadata_floor fail-open 기록."""
    from koipa import config as cfg

    monkeypatch.setattr(cfg.settings, "metadata_floor_enabled", True, raising=False)
    # 상향 게이트가 등급 재계산에 진입하면 예외 → except 로 fail-open.
    monkeypatch.setattr(InferencePipeline, "_enforce_label_consistency", _boom)

    pipe = InferencePipeline()  # rule-fallback (모델 불요)
    before = _gate_val("metadata_floor")
    # 평범한 본문 → rule-fallback 저등급 예측. security_marking=top_secret 이 그보다 높아 floor 진입.
    res = pipe.run(
        text="평범한 사내 안내 문서입니다. 특별한 내용 없음.",
        use_rag=False,
        metadata={"security_marking": "top_secret"},
        return_evidence=False,
    )
    assert res is not None and res.label is not None  # 예외 전파 없이 분류 계속(fail-safe)
    assert _gate_val("metadata_floor") == before + 1


# ── 방향별 fail-secure 전환 (2026-08-12) ───────────────────────────────────────
# 계량만으로는 부족하다. 카운터는 사후에 사람이 대시보드를 봐야 알고, 그 사이 문서는
# 자동확정으로 나간다. 미탐 방향 게이트가 죽으면 그 문서는 사람 검수로 보내야 한다.
# 안전 방향(source_prior_cap = 하향 미적용 → 과분류)은 현행 유지 — 검수부담만 붙는다.


def _warned(result) -> bool:
    return any(P.GATE_FAIL_OPEN_WARNING in w for w in (result.warnings or []))


class _Res:
    """InferenceResult 대역 — warnings 만 있으면 되는 경로라 최소 구현."""

    def __init__(self):
        self.warnings: list[str] = []


@pytest.mark.parametrize(
    "gate",
    ["fnr_safe_override", "ts_tie_break", "metadata_floor", "s2_underclass_risk"],
)
def test_miss_direction_gate_failure_marks_result_for_review(gate):
    result = _Res()
    P._record_gate_fail_open(gate, result)
    assert _warned(result), f"{gate} 는 미탐 방향인데 검수 신호를 안 남겼다"


def test_overclassification_direction_gate_failure_does_not_add_review_burden():
    """source_prior_cap 하향 미적용 = 공개출처가 상위등급 유지 = 과분류(안전 방향).

    여기에 검수를 붙이면 안전 방향 실패에 사람 시간만 쓰게 된다.
    """
    result = _Res()
    P._record_gate_fail_open("source_prior_cap", result)
    assert not _warned(result)


def test_warning_attachment_never_breaks_classification():
    """경고 부착이 실패해도 분류 경로는 계속돼야 한다(fail-safe 유지)."""

    class _Hostile:
        @property
        def warnings(self):
            raise RuntimeError("boom")

    P._record_gate_fail_open("metadata_floor", _Hostile())  # 예외 없으면 통과


def test_metadata_floor_fail_open_routes_real_pipeline_result(monkeypatch):
    """콜사이트 통합 — 실제 run() 결과에 검수 신호가 붙는다."""
    from koipa import config as cfg

    monkeypatch.setattr(cfg.settings, "metadata_floor_enabled", True, raising=False)
    monkeypatch.setattr(InferencePipeline, "_enforce_label_consistency", _boom)

    pipe = InferencePipeline()
    res = pipe.run(
        text="평범한 사내 안내 문서입니다. 특별한 내용 없음.",
        use_rag=False,
        metadata={"security_marking": "top_secret"},
        return_evidence=False,
    )
    assert res is not None and res.label is not None   # fail-safe 유지
    assert _warned(res)                                 # 그리고 검수로 간다


def test_classify_service_routes_gate_fail_open_to_needs_review():
    """pipeline 이 남긴 신호를 classify_service 가 실제로 needs_review 로 바꾸는가.

    경고만 붙고 라우팅이 없으면 아무것도 달라지지 않는다 — 배선을 고정한다.
    """
    import inspect

    from koipa.services import classify_service as cs

    source = inspect.getsource(cs)
    assert 'any("gate-fail-open" in w for w in warnings_acc)' in source
    marker = source.index('any("gate-fail-open" in w for w in warnings_acc)')
    assert 'status = "needs_review"' in source[marker : marker + 400]
