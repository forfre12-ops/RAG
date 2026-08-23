"""RAG ``/answer`` 품질 메트릭 — reference 답변 없이 측정 가능한 자동 지표.

배경(2026-06-02): /answer는 인용을 구조화 데이터(citations)로 내보내고 프롬프트가
인용을 강제하지만, 그 인용이 맞는지·검색 청크에 충실한지 평가하는 코드가 전무했다.
run_answer_e2e는 ``len(answer)>20``만 보고 검색 0건·LLM 무발동(deterministic_fallback)도
통과시켰다 — 합성-청크-투입 버그 수정의 회귀 안전망이 없었음.

본 모듈은 **사람 gold(reference 답변) 없이 지금 측정 가능한** 세 축을 순수 함수로 제공:

1. ``citation_metrics``  — 답변의 ``[n]`` 인용이 제공된 hits 범위(1..n) 안인지.
   범위 초과 = 환각 인용, 인용 0개 = 무근거 답변 신호.
2. ``grounding_signals`` — 답변이 검색 hit에 근거하는지의 구조적 신호.
   zero-hit인데 단정, deterministic_fallback, 인용 마커 부재 등.
3. ``citation_alignment`` — 답변이 인용한 source_doc 집합 vs retrieval_gold
   relevant_doc_ids로 precision/recall/F1 (Citation-F1) — 검색-답변 종단 정합.

reference 답변(expected_answer) 비교(BLEU/ROUGE/LLM-judge groundedness)는 answer_gold가
**사람 검수**로 만들어진 뒤 별도 추가한다. 한국어 자유생성 답변엔 표면 n-gram 중첩보다
임베딩 코사인·LLM-judge가 적합하므로, gold가 생기면 그때 메트릭을 선정한다. 여기서는
데이터 추가 없이 즉시 산출 가능한 지표만 둔다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# 답변 본문에서 [1], [2] 같은 인용 마커 추출.
_CITE_RE = re.compile(r"\[(\d+)\]")

# deterministic fallback 답변이 남기는 표지(rag/answer.py의 _deterministic_answer 문구).
_FALLBACK_MARKERS = (
    "LLM 미사용",
    "결정론적 fallback",
    "검색 결과가 비어",
    "답변을 생성할 수 없습니다",
)


def extract_citations(answer_text: str) -> list[int]:
    """답변 본문에서 인용 번호([n])를 등장 순서대로(중복 제거) 추출."""
    seen: set[int] = set()
    out: list[int] = []
    for m in _CITE_RE.findall(answer_text or ""):
        try:
            n = int(m)
        except ValueError:
            continue
        if n not in seen:
            seen.add(n)
            out.append(n)
    return out


def citation_metrics(answer_text: str, n_hits: int, *, max_index: int | None = None) -> dict:
    """인용 인덱스 정합성.

    Args:
        answer_text: LLM 답변 본문.
        n_hits:      답변에 제공된 검색 hit 수.
        max_index:   프롬프트에 실제 노출된 최대 인용 인덱스(기본 n_hits).
                     answer.py의 _MAX_HITS_IN_PROMPT 캡 때문에 노출 인덱스가
                     n_hits보다 작을 수 있어 분리.

    Returns: {cited, n_cited, out_of_range, cites_no_source}
    """
    allowed = n_hits if max_index is None else min(max_index, n_hits)
    cited = extract_citations(answer_text)
    out_of_range = sorted(c for c in cited if c < 1 or c > allowed)
    return {
        "cited": cited,
        "n_cited": len(cited),
        "out_of_range": out_of_range,
        # hit이 있는데 인용이 하나도 없으면 무근거 답변 신호.
        "cites_no_source": bool(n_hits > 0 and not cited),
    }


def looks_like_fallback(answer_text: str) -> bool:
    """답변 텍스트가 deterministic fallback 표지를 담고 있는지(휴리스틱)."""
    t = answer_text or ""
    return any(m in t for m in _FALLBACK_MARKERS)


def grounding_signals(
    answer_text: str,
    *,
    n_hits: int,
    deterministic_fallback: bool,
) -> dict:
    """답변 근거성의 구조적 신호(사람 판단 불필요).

    Returns: {grounded, hallucination_risk, reasons}
    - grounded: hit이 있고, fallback 아니고, 인용이 범위 내로 존재.
    - hallucination_risk: zero-hit인데 단정적 답변(fallback도 아님) — 환각 위험.
    """
    reasons: list[str] = []
    cm = citation_metrics(answer_text, n_hits)
    is_fallback = bool(deterministic_fallback) or looks_like_fallback(answer_text)

    if n_hits == 0 and not is_fallback:
        reasons.append("zero retrieval hits but answer is assertive (not fallback)")
    if cm["out_of_range"]:
        reasons.append(f"out-of-range citations: {cm['out_of_range']}")
    if cm["cites_no_source"]:
        reasons.append("answer cites no source despite available hits")
    if is_fallback:
        reasons.append("deterministic fallback (LLM unused / empty / zero-hit)")

    grounded = (
        n_hits > 0
        and not is_fallback
        and cm["n_cited"] > 0
        and not cm["out_of_range"]
    )
    hallucination_risk = (n_hits == 0 and not is_fallback) or bool(cm["out_of_range"])
    return {
        "grounded": grounded,
        "hallucination_risk": hallucination_risk,
        "is_fallback": is_fallback,
        "reasons": reasons,
    }


@dataclass
class AlignmentResult:
    precision: float
    recall: float
    f1: float
    n_cited: int
    n_relevant: int
    n_correct: int


def citation_alignment(cited_doc_ids, relevant_doc_ids) -> AlignmentResult:
    """답변이 인용한 source_doc 집합 vs retrieval_gold relevant_doc_ids로 Citation-F1.

    검색-답변 종단 정합: 답변이 '정답 문서'를 실제로 인용했는가. reference 답변
    텍스트 없이 retrieval_gold(이미 80건 존재)만으로 산출 가능.
    """
    cited = {str(d) for d in (cited_doc_ids or []) if d}
    relevant = {str(d) for d in (relevant_doc_ids or []) if d}
    if not cited and not relevant:
        return AlignmentResult(1.0, 1.0, 1.0, 0, 0, 0)
    correct = len(cited & relevant)
    precision = correct / len(cited) if cited else 0.0
    recall = correct / len(relevant) if relevant else 0.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return AlignmentResult(precision, recall, f1, len(cited), len(relevant), correct)


def cited_source_docs(answer_text: str, citations) -> list[str]:
    """답변이 실제 인용한 [n]을 citations(rank→source_doc)에 매핑해 source_doc 목록 반환.

    citations: RagCitation 유사 객체 리스트(.rank, .source_doc) 또는 dict 리스트.
    """
    by_rank: dict[int, str] = {}
    for c in citations or []:
        rank = c.get("rank") if isinstance(c, dict) else getattr(c, "rank", None)
        src = c.get("source_doc") if isinstance(c, dict) else getattr(c, "source_doc", None)
        if rank is not None and src is not None:
            by_rank[int(rank)] = str(src)
    out: list[str] = []
    for n in extract_citations(answer_text):
        if n in by_rank and by_rank[n] not in out:
            out.append(by_rank[n])
    return out
