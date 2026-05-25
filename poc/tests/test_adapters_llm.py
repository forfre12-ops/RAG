"""LLM 어댑터 — Noop provider 동작 + factory + 비용 추정."""

from __future__ import annotations

import pytest

from lloydk.adapters.llm import NoopProvider, build_provider
from lloydk.adapters.llm.base import estimate_cost_usd


def test_noop_provider_is_deterministic():
    """동일 프롬프트 → 동일 출력. 캐시·재현 가능성 보장."""
    p = NoopProvider()
    a = p.generate("코드: TS\n신규 사업 검토", max_tokens=200)
    b = p.generate("코드: TS\n신규 사업 검토", max_tokens=200)
    assert a.text == b.text
    assert a.usage.cost_usd == 0.0
    assert a.usage.input_tokens > 0
    assert a.usage.output_tokens > 0


def test_noop_provider_grade_signal_in_body():
    """등급 코드별로 본문에 해당 등급 핵심 키워드가 포함되어야 룰 라벨러가 매칭."""
    p = NoopProvider()
    ts = p.generate("코드: TS\nM&A 계획").text
    s3 = p.generate("코드: S3\n외부 공지").text
    assert "특급기밀" in ts or "M&A" in ts
    assert "보도자료" in s3 or "공시" in s3
    # 등급 신호 누설(타등급 키워드) 방지
    assert "1급 비밀" not in ts
    assert "특급기밀" not in s3


def test_build_provider_unknown_raises():
    with pytest.raises(ValueError):
        build_provider("nonexistent_provider")


def test_estimate_cost_usd_known_model():
    # 1M input + 1M output for claude-sonnet-4-6 → 3 + 15 = 18
    assert estimate_cost_usd("claude-sonnet-4-6", 1_000_000, 1_000_000) == pytest.approx(18.0)


def test_estimate_cost_usd_unknown_model_is_zero():
    assert estimate_cost_usd("model-not-in-table", 1_000, 1_000) == 0.0
