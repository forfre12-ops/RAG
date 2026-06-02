"""LLMUsageService → Prometheus 토큰·비용 메트릭 연결 검증.

배경(2026-06-02): llm_usage가 DB/JSONL엔 기록하나 Prometheus 노출이 없어
실시간 비용 관측이 불가했음. record()가 카운터를 갱신하는지 고정한다.
카운터는 전역 상태이므로 before/after 델타로 검증.
"""

from __future__ import annotations

import os
import tempfile

from lloydk.adapters.llm.base import UsageRecord
from lloydk.api.prom_metrics import (
    LLM_CALLS_TOTAL,
    LLM_COST_USD_TOTAL,
    LLM_TOKENS_TOTAL,
)
from lloydk.services.llm_usage_service import LLMUsageService


def _v(counter, labels) -> float:
    return counter.labels(*labels)._value.get()


def _svc() -> LLMUsageService:
    path = os.path.join(tempfile.gettempdir(), "llm_usage_metrics_test.jsonl")
    return LLMUsageService(jsonl_path=path)


def test_record_increments_token_and_cost_counters():
    p, m, purpose = "anthropic", "claude-sonnet-4-6", "classify"
    before_in = _v(LLM_TOKENS_TOTAL, (p, m, purpose, "input"))
    before_out = _v(LLM_TOKENS_TOTAL, (p, m, purpose, "output"))
    before_cost = _v(LLM_COST_USD_TOTAL, (p, m, purpose))
    before_calls = _v(LLM_CALLS_TOTAL, (p, m, purpose, "true"))

    _svc().record(
        UsageRecord(provider=p, model=m, input_tokens=120, output_tokens=45, cost_usd=0.0021),
        purpose=purpose,
        tenant_id="t1",
    )

    assert _v(LLM_TOKENS_TOTAL, (p, m, purpose, "input")) == before_in + 120
    assert _v(LLM_TOKENS_TOTAL, (p, m, purpose, "output")) == before_out + 45
    assert abs(_v(LLM_COST_USD_TOTAL, (p, m, purpose)) - (before_cost + 0.0021)) < 1e-9
    assert _v(LLM_CALLS_TOTAL, (p, m, purpose, "true")) == before_calls + 1


def test_failed_call_increments_false_success_label():
    p, m, purpose = "openai", "gpt-4o", "synthesis"
    before = _v(LLM_CALLS_TOTAL, (p, m, purpose, "false"))
    _svc().record(
        UsageRecord(provider=p, model=m, input_tokens=0, output_tokens=0,
                    cost_usd=0.0, success=False, error_code="rate_limit"),
        purpose=purpose,
    )
    assert _v(LLM_CALLS_TOTAL, (p, m, purpose, "false")) == before + 1


def test_zero_tokens_does_not_emit_token_counter():
    """input/output=0이면 토큰 카운터는 갱신 안 함(불필요한 시계열 생성 방지)."""
    p, m, purpose = "noop", "mock", "classify"
    before = _v(LLM_TOKENS_TOTAL, (p, m, purpose, "input"))
    _svc().record(
        UsageRecord(provider=p, model=m, input_tokens=0, output_tokens=0, cost_usd=0.0),
        purpose=purpose,
    )
    assert _v(LLM_TOKENS_TOTAL, (p, m, purpose, "input")) == before  # 불변


def test_record_does_not_raise_when_metrics_unavailable(monkeypatch):
    """Prometheus 카운터가 깨져도 record()는 정상 완료(_emit_metrics 내부 except 흡수)."""
    import lloydk.api.prom_metrics as pm

    class _Boom:
        def labels(self, *a):
            raise RuntimeError("prom down")

    monkeypatch.setattr(pm, "LLM_TOKENS_TOTAL", _Boom())
    # raise 없이 정상 반환해야 함 — 비용 기록이 메트릭 장애에 영향받지 않음.
    _svc().record(
        UsageRecord(provider="x", model="y", input_tokens=1, output_tokens=1, cost_usd=0.0),
        purpose="classify",
    )
