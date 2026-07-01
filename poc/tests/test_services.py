"""ClassifyService + LLMUsageService — 외부 인프라 없이 동작 확인."""

from __future__ import annotations
import pytest
pytestmark = pytest.mark.slow

import json
from pathlib import Path

from lloydk.adapters.llm.base import UsageRecord
from lloydk.schemas.classify import ClassifyRequest
from lloydk.services.classify_service import ClassifyService
from lloydk.services.llm_usage_service import LLMUsageService


def test_classify_service_singleton():
    a = ClassifyService.get_instance()
    b = ClassifyService.get_instance()
    assert a is b


def test_classify_service_end_to_end():
    svc = ClassifyService.get_instance()
    req = ClassifyRequest(
        doc_id="t-1",
        content="특급기밀 핵심 원천기술 M&A 계획 차세대 제품 설계도",
        use_rag=False,
        return_evidence=True,
    )
    res = svc.classify(req)
    assert res.label.value == "TS"
    assert res.confidence > 0.5
    assert res.doc_id == "t-1"
    assert len(res.evidence) > 0


def test_llm_usage_service_writes_jsonl(tmp_path: Path):
    svc = LLMUsageService(jsonl_path=str(tmp_path / "usage.jsonl"))
    rec = UsageRecord(
        provider="noop",
        model="noop",
        input_tokens=100,
        output_tokens=50,
        cost_usd=0.0,
        latency_ms=10,
    )
    svc.record(rec, purpose="test", reference_id="abc")
    svc.record(rec, purpose="test", reference_id="def")

    lines = (tmp_path / "usage.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    parsed = json.loads(lines[0])
    assert parsed["provider"] == "noop"
    assert parsed["purpose"] == "test"
    assert parsed["reference_id"] == "abc"
