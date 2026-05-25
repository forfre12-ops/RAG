"""M1 Synthesis — SyntheticDocGenerator + Noop provider 통합."""

from __future__ import annotations

from lloydk.adapters.llm import NoopProvider
from lloydk.modules.m1_synthesis.generator import SynthRequest, SyntheticDocGenerator


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


def test_grade_consistent_with_target_in_body():
    """Noop은 등급별 키워드를 본문에 포함해야 룰 라벨러가 같은 등급으로 분류."""
    from lloydk.modules.m3_labeling import LabelingPipeline

    gen = SyntheticDocGenerator(llm=NoopProvider())
    labeler = LabelingPipeline()
    for grade in ("TS", "S1", "S2", "S3"):
        doc = gen.generate_one(SynthRequest(target_grade=grade, domain="mixed", count=1))
        full = f"{doc.title}\n\n{doc.body}"
        out = labeler.label(full)
        pred = out.grade.value if hasattr(out.grade, "value") else str(out.grade)
        assert pred == grade, f"target={grade}, predicted={pred}"
