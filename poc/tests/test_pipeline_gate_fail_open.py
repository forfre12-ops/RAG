"""[obs] 서빙 파이프라인(m5_inference) 게이트 fail-open 가시화.

FNR-safe override·source-prior cap·metadata-floor 는 예외 시 등급 조정을 건너뛰고(fail-safe)
분류를 계속한다. 무음이면 상향/라우팅 게이트가 조용히 실패해 비밀이 낮은 등급을 유지했는지
안 보인다 — 예외 fail-open 시 SERVING_GATE_FAIL_OPEN_TOTAL{gate} 증가를 고정("게이트ON=가시성ON").
"""
from __future__ import annotations

import pytest

from lloydk.api import prom_metrics as pm
from lloydk.modules.m5_inference import pipeline as P
from lloydk.modules.m5_inference.pipeline import InferencePipeline


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
        if name == "lloydk.api.prom_metrics":
            raise ImportError("x")
        return real(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _no_metrics)
    P._record_gate_fail_open("metadata_floor")  # 예외 없으면 통과


def test_metadata_floor_fail_open_records_and_stays_fail_safe(monkeypatch):
    """실제 콜사이트: metadata-floor 상향 진입 후 _enforce_label_consistency 예외 →
    분류는 계속(fail-safe, 원 등급 유지) + metadata_floor fail-open 기록."""
    from lloydk import config as cfg

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
