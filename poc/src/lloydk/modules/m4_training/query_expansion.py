"""P2-A3: 쿼리 확장 (Query Expansion).

LLM으로 동의어/약어/영문/관련어를 확장하여 retriever recall 향상.

전략:
- rule-based (즉시): 사전 정의 alias/synonym 사전 + 한국어/영문 ↔ 약어 매핑
- LLM-based (옵션): settings.llm_provider 활용 — 1~3 쿼리 생성
- 결과는 multi-query fanout 후 RRF 또는 weighted union

본 모듈은 어댑터-aware. 쿼리 확장 결과를 받아 vectorstore.search_hybrid 여러 번 호출.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# 한국어 영업비밀 도메인 동의어/약어 사전 (공개 출처 기반)
_RULE_SYNONYMS: dict[str, list[str]] = {
    "M&A": ["인수합병", "기업 인수", "merger", "acquisition"],
    "인수합병": ["M&A", "기업 인수", "merger"],
    "영업비밀": ["trade secret", "TS", "기밀"],
    "특급기밀": ["Top Secret", "TS", "극비"],
    "1급 비밀": ["S1", "Confidential"],
    "2급 대외비": ["S2", "내부공개"],
    "보도자료": ["press release", "공식 발표", "PR"],
    "공시": ["disclosure", "공식 공시"],
    "이사회": ["board meeting", "이사회 의사록"],
    "특허": ["patent", "특허 출원"],
    "임상": ["clinical trial", "임상시험"],
    "반도체": ["semiconductor", "SoC"],
    "원천기술": ["core technology", "fundamental technology"],
    "공급망": ["supply chain", "SCM"],
    "NDA": ["비밀유지계약", "비밀유지", "기밀유지"],
    "MOU": ["양해각서", "memorandum of understanding"],
    "FDA": ["미국 식약청"],
    "원전": ["원자력발전소", "nuclear power plant"],
    "발사체": ["로켓", "launch vehicle"],
    "잠수함": ["submarine"],
    "위성": ["satellite"],
    "수소경제": ["hydrogen economy", "H2 economy"],
    "BCP": ["사업연속성계획", "business continuity"],
    "DR": ["재해복구", "disaster recovery"],
}


@dataclass
class QueryExpansion:
    original: str
    expanded: list[str] = field(default_factory=list)
    method: str = "rule"  # rule | llm | hybrid
    cost_usd: float = 0.0


def expand_rule(query: str, *, max_extra: int = 3) -> QueryExpansion:
    """규칙 기반 — 사전 매칭 동의어 생성. 결정론적."""
    if not query:
        return QueryExpansion(original=query)
    extras: list[str] = []
    q_lower = query.lower()
    for k, syns in _RULE_SYNONYMS.items():
        if k.lower() in q_lower or k in query:
            for s in syns:
                if s not in query and s not in extras:
                    extras.append(s)
                    if len(extras) >= max_extra:
                        break
        if len(extras) >= max_extra:
            break
    expanded_queries = [query] + [f"{query} {x}".strip() for x in extras]
    return QueryExpansion(original=query, expanded=expanded_queries, method="rule")


def expand_llm(query: str, *, n: int = 3, provider=None) -> QueryExpansion:
    """LLM 기반 — n개의 paraphrase/관련 쿼리 생성.

    provider: LLM 어댑터(generate(prompt) → str). None이면 settings.llm_provider 사용.
    실패 시 rule expansion으로 폴백.
    """
    if not query:
        return QueryExpansion(original=query)
    try:
        if provider is None:
            from lloydk.adapters.llm import get_llm  # type: ignore
            provider = get_llm()
    except Exception as e:  # noqa: BLE001
        logger.debug("LLM provider load failed (%s) — falling back to rule", e)
        return expand_rule(query)

    prompt = (
        f"다음 한국어 검색 쿼리에 대해 의미가 비슷한 paraphrase {n}개를 "
        f"JSON 배열로만 반환하세요. 약어·동의어·영문 표기 활용 가능.\n"
        f"원본: {query}"
    )
    try:
        text = provider.generate(prompt)
    except Exception as e:  # noqa: BLE001
        logger.debug("LLM generate failed (%s) — falling back to rule", e)
        return expand_rule(query)

    import json
    try:
        items = json.loads(text)
        if not isinstance(items, list):
            raise ValueError("not a list")
        expanded = [query] + [str(x).strip() for x in items if str(x).strip()][:n]
    except Exception:
        # JSON 파싱 실패 — 줄별 split 시도
        lines = [ln.strip("-· \t\n") for ln in text.splitlines() if ln.strip()]
        expanded = [query] + lines[:n]

    return QueryExpansion(original=query, expanded=expanded, method="llm")


def expand_hybrid(query: str, *, n_llm: int = 2, max_rule: int = 2, provider=None) -> QueryExpansion:
    """rule + LLM 동시 사용. rule이 먼저, LLM이 후. 중복 제거."""
    rule = expand_rule(query, max_extra=max_rule)
    llm = expand_llm(query, n=n_llm, provider=provider)
    seen = set()
    merged: list[str] = []
    for q in rule.expanded + llm.expanded:
        if q not in seen:
            seen.add(q)
            merged.append(q)
    return QueryExpansion(original=query, expanded=merged, method="hybrid", cost_usd=llm.cost_usd)
