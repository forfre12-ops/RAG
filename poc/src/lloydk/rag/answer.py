"""RAG 답안 합성 — query + RAG hits → LLM 응답.

도메인 독립 코어. grade_descriptions를 주입해 어느 프로젝트에서나 재사용 가능.

사용 예:
    from lloydk.rag.answer import synthesize_answer

    result = synthesize_answer(query="계약 위반 기준?", hits=hits)

다른 프로젝트에서 등급 설명을 주입하려면:
    my_descriptions = {"HIGH": "최고 기밀", "MED": "중간 등급", "LOW": "공개 가능"}
    result = synthesize_answer(query=q, hits=hits, grade="HIGH",
                               grade_descriptions=my_descriptions)
"""

from __future__ import annotations

import logging
from typing import Optional

from lloydk.adapters.llm.base import LLMProvider, LLMResponse
from lloydk.schemas.classify import RagContextHit
from lloydk.schemas.rag_answer import (
    RagAnswerResult,
    RagAnswerUsage,
    hits_to_citations,
)

logger = logging.getLogger(__name__)


# 기본 등급 설명 (영업비밀 도메인).
# synthesize_answer(grade_descriptions=...) 로 다른 프로젝트에서 교체 가능.
_DEFAULT_GRADE_DESCRIPTIONS: dict[str, str] = {
    "TS": "특급기밀(TS) — 외부 노출 시 회복 불가능 손실",
    "S1": "1급 비밀(S1) — 핵심 영업비밀, 제한된 인가자만 열람",
    "S2": "2급 대외비(S2) — 부서 단위 통제",
    "S3": "3급 공개(S3) — 외부 공개 가능",
}

_MAX_QUERY_PREVIEW = 800
_MAX_HITS_IN_PROMPT = 5
_MAX_SNIPPET_CHARS = 1000  # 출처 청크 본문 프롬프트 투입 상한


def build_answer_prompt(
    *,
    query: str,
    hits: list[RagContextHit],
    grade: Optional[str] = None,
    grade_descriptions: dict[str, str] | None = None,
) -> str:
    """LLM에 넘길 프롬프트 빌드 — 인용 강제 + 등급 안내.

    Args:
        grade_descriptions: 등급 코드 → 설명 매핑. None이면 기본 영업비밀 설명 사용.
    """
    desc_map = grade_descriptions if grade_descriptions is not None else _DEFAULT_GRADE_DESCRIPTIONS
    query_preview = query.strip()[:_MAX_QUERY_PREVIEW]
    grade_key = grade.value if hasattr(grade, "value") else str(grade) if grade else None
    grade_line = (
        f"\n[분류 등급] {desc_map.get(grade_key, grade_key)}"
        if grade_key is not None
        else ""
    )

    citation_block = ""
    if hits:
        lines = []
        for i, h in enumerate(hits[:_MAX_HITS_IN_PROMPT], start=1):
            snippet = " ".join((getattr(h, "text", "") or "").split())[:_MAX_SNIPPET_CHARS]
            header = f"  [{i}] source={h.source_doc} score={h.score:.3f}"
            lines.append(f"{header}\n      {snippet}" if snippet else header)
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
    grade: Optional[str],
    grade_descriptions: dict[str, str],
) -> str:
    grade_key = grade.value if hasattr(grade, "value") else str(grade) if grade else None
    grade_desc = grade_descriptions.get(grade_key, grade_key) if grade_key else None

    if not hits:
        if grade_desc:
            return (
                f"질문: {query.strip()[:200]}\n"
                f"분류 등급: {grade_desc}.\n"
                "RAG 검색 결과가 비어 있어 추가 근거를 제시할 수 없습니다."
            )
        return (
            f"질문: {query.strip()[:200]}\n"
            "RAG 검색 결과가 비어 있어 답변을 생성할 수 없습니다."
        )

    cite_lines = [
        f"[{i}] {h.source_doc} (chunk={h.chunk_id}, score={h.score:.3f})"
        for i, h in enumerate(hits[:_MAX_HITS_IN_PROMPT], start=1)
    ]
    grade_line = f"\n분류 등급: {grade_desc}." if grade_desc else ""
    return (
        f"질문: {query.strip()[:200]}{grade_line}\n"
        f"관련 출처 {len(hits)}건을 검색했습니다:\n"
        + "\n".join(cite_lines)
        + "\n(LLM 미사용 — 결정론적 fallback 응답)"
    )


def _coerce_provider_text(raw) -> tuple[str, Optional[RagAnswerUsage]]:
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
    return str(raw), None


def synthesize_answer(
    *,
    query: str,
    hits: list[RagContextHit],
    grade: Optional[str] = None,
    provider: Optional[LLMProvider] = None,
    grade_descriptions: dict[str, str] | None = None,
) -> RagAnswerResult:
    """RAG hits + 선택적 분류 등급으로 LLM 답안 합성.

    Args:
        query:             사용자 질의 (필수)
        hits:              RagContextHit 리스트
        grade:             분류 등급 코드 (str 또는 Grade enum)
        provider:          테스트용 명시 주입. None이면 build_provider().
        grade_descriptions: 등급 코드 → 설명 매핑.
                            None이면 기본 영업비밀 설명 사용.
                            다른 프로젝트에서 {"HIGH": "...", "LOW": "..."} 형태로 주입 가능.

    실패·noop·미설정은 deterministic_fallback=True로 반환 (예외 안 던짐).
    """
    desc_map = grade_descriptions if grade_descriptions is not None else _DEFAULT_GRADE_DESCRIPTIONS
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

    chosen: Optional[LLMProvider] = provider
    if chosen is None:
        try:
            from lloydk.adapters.llm import build_provider  # noqa: PLC0415
            chosen = build_provider()
        except Exception as exc:  # noqa: BLE001
            logger.debug("rag_answer: build_provider failed (%s) — deterministic", exc)
            warnings_acc.append(f"provider build failed: {type(exc).__name__}")
            chosen = None

    if chosen is not None and getattr(chosen, "name", "") == "noop":
        warnings_acc.append("noop provider — using deterministic answer")
        chosen = None

    if chosen is None:
        return RagAnswerResult(
            answer=_deterministic_answer(query=query, hits=hits, grade=grade, grade_descriptions=desc_map),
            citations=citations,
            grade=grade,
            usage=None,
            warnings=warnings_acc,
            deterministic_fallback=True,
        )

    prompt = build_answer_prompt(query=query, hits=hits, grade=grade, grade_descriptions=desc_map)
    try:
        raw = chosen.generate(prompt, max_tokens=1024, temperature=0.3)
    except Exception as exc:  # noqa: BLE001
        logger.warning("rag_answer: generate failed (%s) — deterministic fallback", exc)
        warnings_acc.append(f"llm generate failed: {type(exc).__name__}")
        return RagAnswerResult(
            answer=_deterministic_answer(query=query, hits=hits, grade=grade, grade_descriptions=desc_map),
            citations=citations,
            grade=grade,
            usage=None,
            warnings=warnings_acc,
            deterministic_fallback=True,
        )

    # [QW] 답안 합성 LLM 비용 best-effort 기록 (purpose='answer').
    try:
        from lloydk.services.llm_usage_service import record_llm_usage  # noqa: PLC0415
        record_llm_usage(raw, purpose="answer")
    except Exception:  # noqa: BLE001
        pass

    text, usage = _coerce_provider_text(raw)
    if not text or not text.strip():
        warnings_acc.append("llm returned empty text — deterministic fallback")
        return RagAnswerResult(
            answer=_deterministic_answer(query=query, hits=hits, grade=grade, grade_descriptions=desc_map),
            citations=citations,
            grade=grade,
            usage=usage,
            warnings=warnings_acc,
            deterministic_fallback=True,
        )

    # 인용 정합성 검증: 프롬프트는 [n] 인용을 강제하지만(build_answer_prompt) 출력이
    # 계약을 지키는지 한 번도 확인하지 않았다. 답변이 hits 범위 밖 인덱스를 인용하거나
    # (환각 인용) 출처가 있는데 인용을 0개 달면 warnings에 남긴다.
    cite_warnings, out_of_range = _citation_warnings(text, len(hits))
    warnings_acc.extend(cite_warnings)

    # #16: 환각 인용 관측 신호 노출.
    # RagAnswerResult 스키마에 별도 필드를 추가하려면 schemas/rag_answer.py를 고쳐야 하는데
    # 본 작업은 answer.py 단일 파일만 수정 가능하므로, 호출부가 파싱할 수 있도록
    # warnings에 기계 판독용 구조 마커("hallucination_risk:<n>")로 노출한다.
    # n = out-of-range(존재하지 않는 근거 번호) 인용 개수.
    n_out_of_range = len(out_of_range)
    warnings_acc.append(f"hallucination_risk:{n_out_of_range}")

    # #16: 인용 검증을 경고만이 아니라 집행(enforce)으로 승격 — opt-in, 기본 비활성.
    # settings.answer_enforce_citations(config.py에 정식 필드, 기본 False)로 켠다.
    # settings 자체 로드가 실패하는 비정상 환경에서도 기존 동작(경고만) 유지.
    enforce = False
    try:
        from lloydk.config import settings  # noqa: PLC0415

        enforce = bool(settings.answer_enforce_citations)
    except Exception as exc:  # noqa: BLE001 — settings 미구성 시에도 기존 동작 유지
        logger.debug("rag_answer: settings load failed (%s) — enforce off", exc)

    # 심각한 인용 문제 = out-of-range citation 존재(존재하지 않는 근거 번호 인용).
    if enforce and n_out_of_range > 0:
        warnings_acc.append(
            "citation enforcement: out-of-range citation → demoted to deterministic answer"
        )
        return RagAnswerResult(
            answer=_deterministic_answer(query=query, hits=hits, grade=grade, grade_descriptions=desc_map),
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


def _citation_warnings(answer_text: str, n_hits: int) -> tuple[list[str], list[int]]:
    """LLM 답변의 인용 인덱스를 hits 범위와 대조해 경고·out-of-range 목록 생성.

    Returns:
        (warnings, out_of_range): out_of_range는 범위를 벗어난 인용 번호 리스트
        (#16: 호출부가 hallucination_risk 카운트로 재사용).
    """
    from lloydk.modules.m6_evaluation.answer_metrics import citation_metrics  # noqa: PLC0415

    cm = citation_metrics(answer_text, n_hits, max_index=_MAX_HITS_IN_PROMPT)
    out: list[str] = []
    out_of_range: list[int] = list(cm["out_of_range"])
    if out_of_range:
        out.append(
            f"citation out-of-range: {out_of_range} "
            f"(only {min(_MAX_HITS_IN_PROMPT, n_hits)} hits shown) — possible hallucinated citation"
        )
    if cm["cites_no_source"]:
        out.append("answer cites no source despite available hits")
    return out, out_of_range
