from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def _module():
    path = Path(__file__).parents[1] / "scripts" / "summarize_llm_usage.py"
    spec = importlib.util.spec_from_file_location("summarize_llm_usage", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_summary_scopes_totals_to_one_build_sha(tmp_path):
    source = tmp_path / "usage.jsonl"
    source.write_text("\n".join([
        json.dumps({"build_sha": "abc1234", "provider": "openai", "model": "gpt", "purpose": "answer", "billing_phase": "production", "input_tokens": 10, "output_tokens": 4, "cost_usd": 0.002, "success": True, "called_at": "2026-08-19T00:00:00+00:00"}),
        json.dumps({"build_sha": "other", "provider": "noop", "model": "noop", "input_tokens": 999, "output_tokens": 999, "cost_usd": 0, "success": True}),
    ]) + "\n", encoding="utf-8")
    report = _module().summarize(source, "abc1234")
    assert report["records"] == 1
    assert report["totals"] == {"calls": 1, "successful_calls": 1, "failed_calls": 0, "input_tokens": 10, "output_tokens": 4, "cost_usd": 0.002}
    assert report["by_provider_model_purpose"][0]["billing_phase"] == "production"
