"""RAG 답변 품질 메트릭 + 인용 검증 단위 테스트 (lite, 라이브 API 불필요).

대상:
- m6_evaluation.answer_metrics: citation_metrics / grounding_signals /
  citation_alignment / cited_source_docs (사람 gold 없이 산출 가능한 자동 지표)
- rag.answer.synthesize_answer: LLM 답변의 [n] 인용 범위 검증 → warnings
"""

from __future__ import annotations

from lloydk.modules.m6_evaluation.answer_metrics import (
    citation_alignment,
    citation_metrics,
    cited_source_docs,
    extract_citations,
    grounding_signals,
)
from lloydk.schemas.classify import RagContextHit
from lloydk.services.rag_answer_service import synthesize_answer


# ---------------------------------------------------------------------------
# citation_metrics / extract_citations
# ---------------------------------------------------------------------------

def test_extract_citations_dedup_in_order():
    assert extract_citations("근거 [1] 및 [2], 다시 [1].") == [1, 2]
    assert extract_citations("인용 없음") == []


def test_citation_metrics_out_of_range():
    cm = citation_metrics("[1] 맞고 [9] 틀림", n_hits=5)
    assert cm["out_of_range"] == [9]
    assert cm["n_cited"] == 2
    assert cm["cites_no_source"] is False


def test_citation_metrics_cites_no_source():
    cm = citation_metrics("출처 없이 단정함.", n_hits=3)
    assert cm["cites_no_source"] is True
    assert cm["out_of_range"] == []


def test_citation_metrics_max_index_cap():
    # hits는 8건이지만 프롬프트에 5개만 노출됐다면 [7]은 범위 밖.
    cm = citation_metrics("[7] 인용", n_hits=8, max_index=5)
    assert cm["out_of_range"] == [7]


# ---------------------------------------------------------------------------
# grounding_signals
# ---------------------------------------------------------------------------

def test_grounding_signals_grounded():
    g = grounding_signals("근거 [1][2]에 따르면…", n_hits=3, deterministic_fallback=False)
    assert g["grounded"] is True
    assert g["hallucination_risk"] is False


def test_grounding_signals_zero_hit_assertive_is_risk():
    g = grounding_signals("확실히 그렇다.", n_hits=0, deterministic_fallback=False)
    assert g["hallucination_risk"] is True
    assert g["grounded"] is False


def test_grounding_signals_fallback_detected_by_marker():
    g = grounding_signals(
        "RAG 검색 결과가 비어 있어 답변을 생성할 수 없습니다.",
        n_hits=0,
        deterministic_fallback=False,
    )
    assert g["is_fallback"] is True
    # fallback이면 zero-hit 단정 환각으로 치지 않음
    assert g["hallucination_risk"] is False


def test_grounding_signals_out_of_range_is_risk():
    g = grounding_signals("[9] 인용", n_hits=2, deterministic_fallback=False)
    assert g["hallucination_risk"] is True
    assert g["grounded"] is False


# ---------------------------------------------------------------------------
# citation_alignment (Citation-F1) / cited_source_docs
# ---------------------------------------------------------------------------

def test_citation_alignment_perfect():
    r = citation_alignment(["a", "b"], ["a", "b"])
    assert r.f1 == 1.0 and r.precision == 1.0 and r.recall == 1.0


def test_citation_alignment_partial():
    r = citation_alignment(["a", "x"], ["a", "b"])
    assert r.n_correct == 1
    assert r.precision == 0.5
    assert r.recall == 0.5


def test_citation_alignment_both_empty_is_one():
    r = citation_alignment([], [])
    assert r.f1 == 1.0


def test_cited_source_docs_maps_indices():
    citations = [
        {"rank": 1, "source_doc": "doc-A"},
        {"rank": 2, "source_doc": "doc-B"},
        {"rank": 3, "source_doc": "doc-C"},
    ]
    assert cited_source_docs("근거 [1] 및 [3]", citations) == ["doc-A", "doc-C"]


# ---------------------------------------------------------------------------
# synthesize_answer 인용 검증 (fake provider)
# ---------------------------------------------------------------------------

def _hits(n: int = 3) -> list[RagContextHit]:
    return [
        RagContextHit(source_doc=f"doc-{i}", chunk_id=f"c-{i}", score=0.9 - i * 0.1, text=f"본문 {i}")
        for i in range(n)
    ]


class _FakeProvider:
    name = "fake"
    model = "fake-m1"

    def __init__(self, text: str):
        self._text = text

    def generate(self, prompt: str, **kwargs):
        return self._text

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 3)


def test_synthesize_answer_flags_out_of_range_citation():
    res = synthesize_answer(
        query="질문", hits=_hits(3), provider=_FakeProvider("근거 [1] 그리고 [9].")
    )
    assert res.deterministic_fallback is False
    assert any("out-of-range" in w for w in res.warnings)


def test_synthesize_answer_flags_no_citation():
    res = synthesize_answer(
        query="질문", hits=_hits(3), provider=_FakeProvider("출처 없이 단정하는 답변.")
    )
    assert any("cites no source" in w for w in res.warnings)


def test_synthesize_answer_clean_citations_no_warning():
    res = synthesize_answer(
        query="질문", hits=_hits(3), provider=_FakeProvider("근거 [1]과 [2]에 따르면 그렇다.")
    )
    assert not any("out-of-range" in w or "cites no source" in w for w in res.warnings)
