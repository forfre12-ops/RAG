"""[QW] LLM 비용추적 배선 — record_llm_usage 헬퍼 + 경로별 호출지점.

LLMUsageService.record가 src 어디서도 호출되지 않아 모든 LLM 비용이 미기록이었다.
헬퍼가 LLMResponse(.usage)/UsageRecord를 받아 Prometheus 비용·호출 카운터를 갱신하고,
query_expansion 등 실제 호출지점에서 purpose 라벨로 분리 집계되는지 고정한다.
"""

from __future__ import annotations

from lloydk.adapters.llm.base import LLMResponse, UsageRecord
from lloydk.api import prom_metrics as pm
from lloydk.services.llm_usage_service import LLMUsageService, record_llm_usage


def _usage(*, cost=0.01, provider="anthropic", model="claude-opus-4-8", it=100, ot=50):
    return UsageRecord(provider=provider, model=model, input_tokens=it, output_tokens=ot, cost_usd=cost)


def _calls(provider, model, purpose, success="true"):
    return pm.LLM_CALLS_TOTAL.labels(provider, model, purpose, success)._value.get()


def _cost(provider, model, purpose):
    return pm.LLM_COST_USD_TOTAL.labels(provider, model, purpose)._value.get()


def test_record_from_llm_response_increments_calls_and_cost(tmp_path):
    svc = LLMUsageService(jsonl_path=str(tmp_path / "u.jsonl"))
    resp = LLMResponse(text="hi", usage=_usage(cost=0.02))
    c0 = _calls("anthropic", "claude-opus-4-8", "synthesis")
    cost0 = _cost("anthropic", "claude-opus-4-8", "synthesis")
    record_llm_usage(resp, purpose="synthesis", service=svc)
    assert _calls("anthropic", "claude-opus-4-8", "synthesis") == c0 + 1
    assert abs(_cost("anthropic", "claude-opus-4-8", "synthesis") - (cost0 + 0.02)) < 1e-9


def test_record_from_raw_usage_record(tmp_path):
    svc = LLMUsageService(jsonl_path=str(tmp_path / "u.jsonl"))
    c0 = _calls("openai", "gpt-4o", "answer")
    record_llm_usage(_usage(provider="openai", model="gpt-4o"), purpose="answer", service=svc)
    assert _calls("openai", "gpt-4o", "answer") == c0 + 1


def test_record_noop_on_text_only_or_none(tmp_path):
    svc = LLMUsageService(jsonl_path=str(tmp_path / "u.jsonl"))
    # usage 없는 입력 → 예외 없이 no-op.
    record_llm_usage("just text", purpose="answer", service=svc)
    record_llm_usage(None, purpose="answer", service=svc)


def test_billing_phase_from_settings(tmp_path, monkeypatch):
    from lloydk import config as cfg
    monkeypatch.setattr(cfg.settings, "llm_billing_phase", "production", raising=False)
    svc = LLMUsageService(jsonl_path=str(tmp_path / "u.jsonl"))
    # billing_phase 미지정 → settings 사용. JSONL에 production 기록 확인.
    record_llm_usage(_usage(provider="openai", model="gpt-4o"), purpose="answer", service=svc)
    content = (tmp_path / "u.jsonl").read_text(encoding="utf-8")
    assert '"billing_phase": "production"' in content


def test_expand_llm_records_query_expansion_usage(tmp_path, monkeypatch):
    import lloydk.services.llm_usage_service as us
    from lloydk.rag import query_expansion as qe
    monkeypatch.setattr(us, "_default_service", us.LLMUsageService(jsonl_path=str(tmp_path / "u.jsonl")))

    class _Stub:
        name = "anthropic"

        def generate(self, prompt, **k):
            return LLMResponse(text='["쿼리1","쿼리2"]', usage=_usage(provider="anthropic", model="claude-opus-4-8", cost=0.005))

    c0 = _calls("anthropic", "claude-opus-4-8", "query_expansion")
    out = qe.expand_llm("테스트 쿼리", provider=_Stub())
    assert out.method == "llm"
    assert _calls("anthropic", "claude-opus-4-8", "query_expansion") == c0 + 1
