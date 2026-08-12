"""backward compat shim — 실제 구현은 koipa.rag.retrieval로 이동됨.

실 호출부(api/answer.py · m5_inference/pipeline.py)는 이 모듈에서
expand_then_search를 import하므로, #29 비즈니스 span을 여기서 얇게 부착한다.
시그니처는 원본과 100% 동일(키워드 위임) — 호출부 변경 불필요. OTel 미설치/미활성
시 span()은 완전 no-op이라 기존 동작·테스트를 그대로 보존한다.
"""
from koipa.obs.otel import span  # #29: 비즈니스(수동) span 헬퍼 — 미설치/미활성 시 no-op
from koipa.rag.retrieval import (  # noqa: F401
    _rerank_hits,
    _rrf_combine,
)
from koipa.rag.retrieval import expand_then_search as _expand_then_search


def expand_then_search(**kwargs):
    """retrieval facade — 검색 진입을 span으로 가시화하고 원 구현에 위임.

    #29: ML/RAG 검색 내부(확장→hybrid→RRF→rerank)는 자동계측만으로 블랙박스다.
    민감정보(쿼리 본문·payload)는 부착하지 않고, 비민감 식별/파라미터만 노출한다
    (컬렉션명·method·top_k·reranker 사용여부). 위치인자 우회를 막기 위해 원 facade가
    keyword-only이므로 **kwargs로 그대로 전달한다.
    """
    with span(
        "rag.expand_then_search",
        **{
            "koipa.rag.collection": kwargs.get("collection"),
            "koipa.rag.method": kwargs.get("method", "rule"),
            "koipa.rag.top_k": kwargs.get("top_k", 5),
            "koipa.rag.use_reranker": bool(kwargs.get("use_reranker", True)),
        },
    ):
        return _expand_then_search(**kwargs)


__all__ = ["expand_then_search", "_rrf_combine", "_rerank_hits"]
