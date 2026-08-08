"""M1 Synthesis — SyntheticDocGenerator + Noop provider 통합."""

from __future__ import annotations

import pytest

from lloydk.adapters.llm import NoopProvider
from lloydk.modules.m1_synthesis import generator as generator_module
from lloydk.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator


def test_generator_rejects_damaged_static_prompt_before_provider_use(monkeypatch):
    monkeypatch.setattr(
        generator_module,
        "SYSTEM_PROMPT",
        "\u5f02\u5e38 decoded prompt without Hangul",
    )
    with pytest.raises(
        generator_module.PromptLanguageContractError,
        match="UTF-8/Hangul integrity gate",
    ):
        SyntheticDocGenerator(llm=NoopProvider())


def test_generate_one_parses_noop_json():
    gen = SyntheticDocGenerator(llm=NoopProvider())
    doc = gen.generate_one(SynthRequest(target_grade="TS", domain="tech", count=1))
    assert doc.target_grade == "TS"
    assert doc.title
    assert doc.body
    assert doc.parse_error is None
    assert doc.usage is not None
    assert doc.usage.cost_usd == 0.0


def test_generate_one_no_pii_violations_for_noop():
    gen = SyntheticDocGenerator(llm=NoopProvider())
    doc = gen.generate_one(SynthRequest(target_grade="S1", domain="hr", count=1))
    assert doc.pii_violations == []


def test_generate_multiple_count_matches():
    gen = SyntheticDocGenerator(llm=NoopProvider())
    docs = gen.generate(SynthRequest(target_grade="S2", domain="business", count=3))
    assert len(docs) == 3
    for d in docs:
        assert d.target_grade == "S2"
        assert d.domain == "business"


def test_noop_v2_no_grade_keyword_leakage():
    """V2 Noop — 등급 키워드를 본문에 주입하지 않음 (leakage 차단 확인).

    V1(구): 등급 키워드 주입 → 룰 라벨러 match 의도.
    V2(현): 도메인 중립 내용 생성 → 등급 키워드 없음. 룰 라벨러 match 보장하지 않음.
    noop은 파이프라인 연결 테스트 전용이며 품질 평가에 사용하지 않는다.
    """
    import re

    GRADE_KEYWORD_PATTERN = re.compile(
        r"특급기밀|1급\s*비밀|2급\s*비밀|대외비|\[TS\]|\[S1\]|\[S2\]|\[S3\]"
        r"|특급기밀\s*관련\s*사항"
    )

    gen = SyntheticDocGenerator(llm=NoopProvider())
    for grade in ("TS", "S1", "S2", "S3"):
        doc = gen.generate_one(
            SynthRequest(target_grade=grade, domain="mixed", count=1)
        )
        full = f"{doc.title}\n\n{doc.body}"
        assert doc.parse_error is None, f"noop V2 JSON 파싱 실패 (grade={grade})"
        assert not GRADE_KEYWORD_PATTERN.search(full), (
            f"grade={grade}: noop V2가 등급 키워드를 본문에 포함 — leakage 차단 실패\n"
            f"  match: {GRADE_KEYWORD_PATTERN.search(full).group() if GRADE_KEYWORD_PATTERN.search(full) else ''}"
        )
