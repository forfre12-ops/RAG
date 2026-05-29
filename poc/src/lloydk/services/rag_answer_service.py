"""RAG 답안 합성 — query + RAG hits → LLM 응답.

ClassifyService와 분리된 별도 서비스. 분류 응답에는 영향 없음.

흐름:
  1. query + hits + 선택적 grade를 prompt로 빌드
  2. build_provider() 또는 호출자 주입 provider로 generate()
  3. provider.text를 그대로 answer로 사용, hits → citations 변환
  4. provider 실패·noop 등 안전하지 않은 경로는 deterministic fallback
     (LLM 없이 인용 출처 나열 + grade 안내만 반환)

원칙:
- LLM 실패는 silent fallback. 호출자는 result.warnings + deterministic_fallback로 감지.
- noop provider는 합성 데이터 JSON을 뱉도록 설계된 것이라 답안 용도엔 부적합 →
  noop 감지 시 즉시 deterministic 분기.
- provider가 LLMResponse가 아닌 raw string을 반환해도 받아들임(테스트 편의).
"""

from __future__ import annotations

import logging
from typing import Optional

from lloydk.adapters.llm.base import LLMProvider, LLMResponse
from lloydk.schemas.classify import RagContextHit
from lloydk.schemas.common import Grade
from lloydk.schemas.rag_answer import (
    RagAnswerResult,
    RagAnswerUsage,
    hits_to_citations,
)

logger = logging.getLogger(__name__)


_GRADE_DESCRIPTION = {
    Grade.TS: "특급기밀(TS) — 외부 노출 시 회복 불가능 손실",
    Grade.S1: "1급 비밀(S1) — 핵심 영업비밀, 제한된 인가자만 열람",
    Grade.S2: "2급 대외비(S2) — 부서 단위 통제",
    Grade.S3: "3급 공개(S3) — 외부 공개 가능",
}


_MAX_QUERY_PREVIEW = 800
_MAX_HITS_IN_PROMPT = 5


def build_answer_prompt(
    *,
    query: str,
    hits: list[RagContextHit],
    grade: Optional[Grade] = None,
) -> str:
    """LLM에 넘길 프롬프트 빌드 — 인용 강제 + 등급 안내."""
    query_preview = query.strip()[:_MAX_QUERY_PREVIEW]
    grade_line = (
        f"\n[분류 등급] {_GRADE_DESCRIPTION.get(grade, grade.value if grade else '미정')}"
        if grade is not None
        else ""
    )

    citation_block = ""
    if hits:
        lines = []
        for i, h in enumerate(hits[:_MAX_HITS_IN_PROMPT], start=1):
            lines.append(
                f"  [{i}] source={h.source_doc} chunk={h.chunk_id} score={h.score:.3f}"
            )
        citation_block = "\n[인용 가능 출처]\n" + "\n".join(lines)

    rules = (
        "\n[지시]\n"
        "1) 위 출처에서만 사실을 근거로 답하라. 출처에 없는 내용은 추정하지 마라.\n"
        "2) 답변 안에 [1], [2] 같은 인용 번호를 명시하라.\n"
        "3) 분류 등급이 주어졌다면 등급 사유를 1~2문장으로 설명하라.\n"
        "4) 한국어로 4~8문장, 간결하게."
    )

    return f"[질문]\n{query_preview}{grade_line}{citation_block}{rules}"


def _deterministic_answer(
    *,
    query: str,
    hits: list[RagContextHit],
    grade: Optional[Grade],
) -> str:
    """LLM 미사용 시 결정론적 답안 — 인용 출처와 등급만 안내."""
    if not hits:
        if grade is not None:
            return (
                f"질문: {query.strip()[:200]}\n"
                f"분류 등급: {_GRADE_DESCRIPTION[grade]}.\n"
                "RAG 검색 결과가 비어 있어 추가 근거를 제시할 수 없습니다."
            )
        return (
            f"질문: {query.strip()[:200]}\n"
            "RAG 검색 결과가 비어 있어 답변을 생성할 수 없습니다."
        )

    cite_lines = []
    for i, h in enumerate(hits[:_MAX_HITS_IN_PROMPT], start=1):
        cite_lines.append(f"[{i}] {h.source_doc} (chunk={h.chunk_id}, score={h.score:.3f})")

    grade_line = (
        f"\n분류 등급: {_GRADE_DESCRIPTION[grade]}."
        if grade is not None
        else ""
    )
    return (
        f"질문: {query.strip()[:200]}{grade_line}\n"
        f"관련 출처 {len(hits)}건을 검색했습니다:\n"
        + "\n".join(cite_lines)
        + "\n(LLM 미사용 — 결정론적 fallback 응답)"
    )


def _coerce_provider_text(raw) -> tuple[str, Optional[RagAnswerUsage]]:
    """provider.generate 반환을 (text, usage)로 정규화.

    LLMResponse면 .text/.usage 사용, raw string이면 text만, 그 외 str() 처리.
    """
    if isinstance(raw, LLMResponse):
        usage = RagAnswerUsage(
            provider=raw.usage.provider,
            model=raw.usage.model,
            input_tokens=int(raw.usage.input_tokens),
            output_tokens=int(raw.usage.output_tokens),
            cost_usd=float(raw.usage.cost_usd),
            latency_ms=int(raw.usage.latency_ms),
            success=bool(raw.usage.success),
        )
        return raw.text, usage
    if isinstance(raw, str):
        return raw, None
    # 모름 — str() 후 usage 없음
    return str(raw), None


def synthesize_answer(
    *,
    query: str,
    hits: list[RagContextHit],
    grade: Optional[Grade] = None,
    provider: Optional[LLMProvider] = None,
) -> RagAnswerResult:
    """RAG hits + 선택적 분류 등급으로 LLM 답안 합성.

    Args:
        query:    사용자 질의 (필수)
        hits:     RagContextHit 리스트. 빈 리스트면 deterministic 안내만.
        grade:    분류 등급(있으면 프롬프트·결과에 반영)
        provider: 테스트용 명시 주입. None이면 build_provider() — settings.llm_provider 따라감.
                  noop은 합성 데이터 JSON을 뱉도록 만든 어댑터라 답안에 부적합 → deterministic 분기.

    실패·noop·미설정은 deterministic_fallback=True로 반환 (예외 안 던짐).
    citations는 hits 순서 그대로 rank 부여 — provider가 추가 가공해도 본 함수는 표시 의무만.
    """
    warnings_acc: list[str] = []
    citations = hits_to_citations(hits)

    if not query or not query.strip():
        return RagAnswerResult(
            answer="질의가 비어 있어 답변을 생성할 수 없습니다.",
            citations=[],
            grade=grade,
            usage=None,
            warnings=["query empty"],
            deterministic_fallback=True,
        )

    # provider 결정
    chosen: Optional[LLMProvider] = provider
    if chosen is None:
        try:
            from lloydk.adapters.llm import build_provider  # noqa: PLC0415

            chosen = build_provider()
        except Exception as exc:  # noqa: BLE001
            logger.debug("rag_answer: build_provider failed (%s) — deterministic", exc)
            warnings_acc.append(f"provider build failed: {type(exc).__name__}")
            chosen = None

    # noop은 답안용으로 쓸 수 없음 — deterministic 분기
    if chosen is not None and getattr(chosen, "name", "") == "noop":
        warnings_acc.append("noop provider — using deterministic answer")
        chosen = None

    if chosen is None:
        return RagAnswerResult(
            answer=_deterministic_answer(query=query, hits=hits, grade=grade),
            citations=citations,
            grade=grade,
            usage=None,
            warnings=warnings_acc,
            deterministic_fallback=True,
        )

    prompt = build_answer_prompt(query=query, hits=hits, grade=grade)
    try:
        raw = chosen.generate(prompt, max_tokens=1024, temperature=0.3)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rag_answer: generate failed (%s) — deterministic fallback", exc)
        warnings_acc.append(f"llm generate failed: {type(exc).__name__}")
        return RagAnswerResult(
            answer=_deterministic_answer(query=query, hits=hits, grade=grade),
            citations=citations,
            grade=grade,
            usage=None,
            warnings=warnings_acc,
            deterministic_fallback=True,
        )

    text, usage = _coerce_provider_text(raw)
    if not text or not text.strip():
        warnings_acc.append("llm returned empty text — deterministic fallback")
        return RagAnswerResult(
            answer=_deterministic_answer(query=query, hits=hits, grade=grade),
            citations=citations,
            grade=grade,
            usage=usage,
            warnings=warnings_acc,
            deterministic_fallback=True,
        )

    return RagAnswerResult(
        answer=text.strip(),
        citations=citations,
        grade=grade,
        usage=usage,
        warnings=warnings_acc,
        deterministic_fallback=False,
    )
