"""RAG 답안 합성 서비스 단위 테스트.

테스트 범위:
- build_answer_prompt: query/hits/grade 반영, 인용 번호 포맷
- synthesize_answer:
  * 빈 query → 안내 + deterministic_fallback
  * 빈 hits → 출처 비어 있음 안내 + deterministic_fallback
  * noop provider → deterministic 분기 (LLM 호출 안 함)
  * fake provider 성공 → answer는 provider.text, usage 채워짐
  * fake provider raise → deterministic fallback + warning
  * fake provider 빈 응답 → deterministic fallback
  * provider가 raw str 반환 → answer 그대로, usage None
  * citations rank는 hits 순서 (1-based)
"""

from __future__ import annotations

import time

from koipa.adapters.llm.base import LLMResponse, UsageRecord
from koipa.adapters.llm.noop_provider import NoopProvider
from koipa.schemas.classify import RagContextHit
from koipa.schemas.common import Grade
from koipa.services.rag_answer_service import (
    build_answer_prompt,
    synthesize_answer,
)


# ------------------------------------------------------------
# 헬퍼 fixtures
# ------------------------------------------------------------


def _hits(n: int = 3) -> list[RagContextHit]:
    return [
        RagContextHit(source_doc=f"doc-{i}", chunk_id=f"chunk-{i}", score=0.9 - i * 0.1)
        for i in range(n)
    ]


class _FakeProvider:
    """generate가 호출되면 미리 설정한 응답을 돌려주는 가짜 provider."""

    name = "fake"
    model = "fake-m1"

    def __init__(self, response=None, *, raises: Exception | None = None):
        self._response = response
        self._raises = raises
        self.calls: list[tuple[str, dict]] = []

    def generate(self, prompt: str, **kwargs):
        self.calls.append((prompt, kwargs))
        if self._raises:
            raise self._raises
        return self._response

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 3)


def _llm_response(text: str) -> LLMResponse:
    return LLMResponse(
        text=text,
        usage=UsageRecord(
            provider="fake",
            model="fake-m1",
            input_tokens=12,
            output_tokens=34,
            cost_usd=0.0012,
            latency_ms=int(time.perf_counter() * 0) + 5,
            success=True,
        ),
        meta={},
    )


# ------------------------------------------------------------
# build_answer_prompt
# ------------------------------------------------------------


def test_prompt_includes_query_and_grade_and_citations():
    p = build_answer_prompt(query="M&A 인수 절차?", hits=_hits(2), grade=Grade.S1)
    assert "M&A 인수 절차" in p
    assert "1급 비밀" in p  # grade 설명 노출
    assert "[1] source=doc-0" in p
    assert "[2] source=doc-1" in p
    assert "[지시]" in p


def test_prompt_truncates_long_query():
    long = "가" * 2000
    p = build_answer_prompt(query=long, hits=[], grade=None)
    # 800자까지만 노출
    assert p.count("가") <= 800


def test_prompt_handles_no_hits_no_grade():
    p = build_answer_prompt(query="간단 질의", hits=[], grade=None)
    assert "간단 질의" in p
    assert "[인용 가능 출처]" not in p
    assert "[분류 등급]" not in p


def test_prompt_caps_hits_at_5():
    p = build_answer_prompt(query="q", hits=_hits(10), grade=None)
    # 5건만 인용 블록에 노출
    assert "[5]" in p
    assert "[6]" not in p


# ------------------------------------------------------------
# synthesize_answer — 입력 가드
# ------------------------------------------------------------


def test_empty_query_returns_warning_fallback():
    r = synthesize_answer(query="   ", hits=_hits(1))
    assert r.deterministic_fallback is True
    assert r.usage is None
    assert "query empty" in r.warnings
    assert r.citations == []  # 빈 query면 citations도 의미 없음


def test_empty_hits_deterministic_with_grade():
    r = synthesize_answer(
        query="질의",
        hits=[],
        grade=Grade.TS,
        provider=_FakeProvider(_llm_response("ANSWER")),
    )
    # 빈 hits여도 provider가 있으면 LLM 호출은 함 — answer는 ANSWER
    assert r.deterministic_fallback is False
    assert r.answer == "ANSWER"
    assert r.citations == []
    assert r.grade is Grade.TS


def test_empty_hits_no_provider_returns_grade_notice():
    r = synthesize_answer(query="질의", hits=[], grade=Grade.S2, provider=NoopProvider())
    # noop은 deterministic 분기
    assert r.deterministic_fallback is True
    assert "2급 대외비" in r.answer
    assert "RAG 검색 결과가 비어" in r.answer
    assert r.citations == []


# ------------------------------------------------------------
# synthesize_answer — noop 분기
# ------------------------------------------------------------


def test_noop_provider_does_not_call_generate():
    noop = NoopProvider()
    # NoopProvider.generate를 호출 가로채기
    called = {"n": 0}
    orig = noop.generate

    def spy(*a, **kw):
        called["n"] += 1
        return orig(*a, **kw)

    noop.generate = spy  # type: ignore[assignment]
    r = synthesize_answer(query="질의", hits=_hits(2), provider=noop)
    assert r.deterministic_fallback is True
    assert called["n"] == 0
    assert "noop provider" in " ".join(r.warnings)


# ------------------------------------------------------------
# synthesize_answer — provider 성공/실패
# ------------------------------------------------------------


def test_provider_success_uses_llm_response_text():
    fake = _FakeProvider(_llm_response("이것은 LLM 답안입니다."))
    r = synthesize_answer(query="q", hits=_hits(2), grade=Grade.S1, provider=fake)

    assert r.deterministic_fallback is False
    assert r.answer == "이것은 LLM 답안입니다."
    assert r.usage is not None
    assert r.usage.provider == "fake"
    assert r.usage.input_tokens == 12
    assert r.usage.output_tokens == 34
    assert r.grade is Grade.S1
    assert len(fake.calls) == 1


def test_provider_raises_falls_back_deterministic():
    fake = _FakeProvider(raises=RuntimeError("network down"))
    r = synthesize_answer(query="q", hits=_hits(2), provider=fake)

    assert r.deterministic_fallback is True
    assert any("llm generate failed" in w for w in r.warnings)
    assert "관련 출처 2건" in r.answer
    # citations은 fallback 경로에서도 유지
    assert len(r.citations) == 2


def test_provider_returns_empty_text_falls_back():
    fake = _FakeProvider(_llm_response("   "))
    r = synthesize_answer(query="q", hits=_hits(1), provider=fake)

    assert r.deterministic_fallback is True
    assert any("empty text" in w for w in r.warnings)
    # usage는 빈 응답이라도 LLMResponse에서 추출됐으므로 보존
    assert r.usage is not None


def test_provider_returns_raw_string():
    fake = _FakeProvider("raw string answer")  # LLMResponse가 아닌 그냥 str
    r = synthesize_answer(query="q", hits=_hits(1), provider=fake)

    assert r.deterministic_fallback is False
    assert r.answer == "raw string answer"
    assert r.usage is None  # raw str엔 usage 없음


# ------------------------------------------------------------
# citations rank 순서
# ------------------------------------------------------------


def test_citations_rank_follows_hits_order():
    hits = _hits(4)
    fake = _FakeProvider(_llm_response("ANS"))
    r = synthesize_answer(query="q", hits=hits, provider=fake)
    assert [c.rank for c in r.citations] == [1, 2, 3, 4]
    assert [c.source_doc for c in r.citations] == [h.source_doc for h in hits]


def test_to_dict_serializable():
    fake = _FakeProvider(_llm_response("ANS"))
    r = synthesize_answer(query="q", hits=_hits(1), provider=fake)
    d = r.model_dump()
    assert d["answer"] == "ANS"
    assert d["citations"][0]["rank"] == 1
    assert d["deterministic_fallback"] is False


# ------------------------------------------------------------
# config: answer_enforce_citations 정식 필드 (기본 OFF, 동작 보존)
# ------------------------------------------------------------


def test_answer_enforce_citations_field_defaults_off():
    """answer_enforce_citations가 Settings 정식 필드이고 기본값 False인지 확인."""
    from koipa.config import Settings

    s = Settings()
    assert hasattr(s, "answer_enforce_citations")
    assert s.answer_enforce_citations is False
    # bool 필드여야 함 (미문서화 env 폴백이 아니라 정식 스키마).
    # model_fields는 인스턴스가 아니라 클래스에서 접근(Pydantic v2.11 deprecation 회피).
    assert Settings.model_fields["answer_enforce_citations"].annotation is bool
