"""[C16] SynthDoc.label_source — noop_fallback↔llm_nonjson 식별 (doc/code 드리프트 해소).

종전 generator docstring은 'label_source: noop_fallback'로 식별된다고 했으나 그 필드가
없었고, parse_error가 noop placeholder와 실 LLM 비-JSON을 뭉뚱그렸다(grep 식별 불가).
DB/실 LLM 불필요 — 페이크 provider로 세 경로를 직접 검증.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from lloydk.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator


class _FakeLLM:
    name = "fake"

    def __init__(self, text: str) -> None:
        self._text = text

    def generate(self, prompt, system=None, temperature=None, max_tokens=None):
        return SimpleNamespace(text=self._text, usage=None)


def test_noop_fallback_marked(monkeypatch) -> None:
    """resp.text 비면 placeholder 본문 + label_source='noop_fallback'(학습 금지 마커)."""
    gen = SyntheticDocGenerator(llm=_FakeLLM(""))
    d = gen.generate_one(SynthRequest(target_grade="S2", domain="인사"))
    assert d.label_source == "noop_fallback"
    assert d.parse_error == "non-json response"
    assert "Noop fallback" in d.body  # placeholder 본문


def test_llm_nonjson_marked() -> None:
    """실 LLM이 비-JSON 텍스트를 주면 raw가 body, label_source='llm_nonjson'."""
    gen = SyntheticDocGenerator(llm=_FakeLLM("이건 JSON이 아니라 평문 응답입니다."))
    d = gen.generate_one(SynthRequest(target_grade="S2", domain="인사"))
    assert d.label_source == "llm_nonjson"
    assert d.parse_error == "non-json response"
    assert d.body == "이건 JSON이 아니라 평문 응답입니다."


def test_parsed_json_has_no_label_source() -> None:
    """정상 JSON 파싱 시 label_source=None, parse_error=None."""
    payload = json.dumps(
        {"title": "t", "body": "정상 본문", "document_type": "보고서", "rationale_tags": ["S2"]},
        ensure_ascii=False,
    )
    gen = SyntheticDocGenerator(llm=_FakeLLM(payload))
    d = gen.generate_one(SynthRequest(target_grade="S2", domain="인사"))
    assert d.label_source is None
    assert d.parse_error is None
    assert d.body == "정상 본문"
