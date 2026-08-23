"""[obs] 검색 품질 저하 폴백 가시화 — 무음으로 떨어지던 폴백을 메트릭으로 노출.

reranker 실패→RRF, encode 실패→skip, 쿼리확장 LLM 실패→룰. 전부 RAG_CONTEXT_FAILURE_TOTAL
{stage}로 best-effort 증가. 폴백 헬퍼 + expand_llm 통합으로 실제 발화를 고정.
"""

from __future__ import annotations

from koipa.api import prom_metrics as pm
from koipa.rag import query_expansion as qe
from koipa.rag.retrieval import _record_fallback


def _stage_val(stage) -> float:
    v = pm.registry.get_sample_value(
        "koipa_rag_context_failure_total", {"stage": stage}
    )
    return 0.0 if v is None else v


def test_record_fallback_increments_stage():
    for stage in ("reranker_fallback", "encode_batch_fallback", "encode_fallback"):
        before = _stage_val(stage)
        _record_fallback(stage)
        assert _stage_val(stage) == before + 1


def test_record_fallback_best_effort_on_bad_registry(monkeypatch):
    # prom_metrics import 실패해도 예외 전파 없음(검색 경로 무영향).
    import builtins
    real_import = builtins.__import__

    def _boom(name, *a, **k):
        if name == "koipa.api.prom_metrics":
            raise ImportError("boom")
        return real_import(name, *a, **k)

    monkeypatch.setattr(builtins, "__import__", _boom)
    _record_fallback("reranker_fallback")  # 예외 없으면 통과


def test_expand_llm_provider_load_failure_records_fallback(monkeypatch):
    # provider=None → build_provider 강제 실패 → 룰 폴백 + 메트릭.
    import koipa.adapters.llm as llm_mod

    def _boom():
        raise RuntimeError("no provider")

    monkeypatch.setattr(llm_mod, "build_provider", _boom)
    before = _stage_val("query_expansion_fallback")
    out = qe.expand_llm("테스트 쿼리", provider=None)
    assert out.method == "rule"  # 룰로 폴백
    assert _stage_val("query_expansion_fallback") == before + 1


def test_expand_llm_generate_failure_records_fallback():
    class _Stub:
        name = "x"

        def generate(self, prompt, **k):
            raise RuntimeError("gen fail")

    before = _stage_val("query_expansion_fallback")
    out = qe.expand_llm("테스트", provider=_Stub())
    assert out.method == "rule"
    assert _stage_val("query_expansion_fallback") == before + 1
