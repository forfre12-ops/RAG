"""LLM adapter factory and deterministic noop provider tests."""

from __future__ import annotations

import pytest

from lloydk.adapters.llm import NoopProvider, build_provider
from lloydk.adapters.llm.base import estimate_cost_usd


def test_noop_provider_is_deterministic():
    p = NoopProvider()
    a = p.generate("code: TS\nnew business review", max_tokens=200)
    b = p.generate("code: TS\nnew business review", max_tokens=200)
    assert a.text == b.text
    assert a.usage.cost_usd == 0.0
    assert a.usage.input_tokens > 0
    assert a.usage.output_tokens > 0


def test_noop_provider_avoids_grade_signal_leakage():
    """Noop output is for plumbing tests, not grade-quality evaluation."""
    p = NoopProvider()
    ts = p.generate("code: TS\nM&A plan").text
    s3 = p.generate("code: S3\npublic notice").text
    assert ts == s3
    assert "title" in ts
    assert "body" in ts
    assert "M&A" not in ts
    assert "TS" not in ts
    assert "S3" not in s3
    assert "Top Secret" not in ts


def test_build_provider_unknown_raises():
    with pytest.raises(ValueError):
        build_provider("nonexistent_provider")


def test_estimate_cost_usd_known_model():
    assert estimate_cost_usd("claude-sonnet-4-6", 1_000_000, 1_000_000) == pytest.approx(18.0)


def test_estimate_cost_usd_unknown_model_is_zero():
    assert estimate_cost_usd("model-not-in-table", 1_000, 1_000) == 0.0
