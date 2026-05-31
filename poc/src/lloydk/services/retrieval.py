"""backward compat shim — 실제 구현은 lloydk.rag.retrieval로 이동됨."""
from lloydk.rag.retrieval import (  # noqa: F401
    _rerank_hits,
    _rrf_combine,
    expand_then_search,
)

__all__ = ["expand_then_search", "_rrf_combine", "_rerank_hits"]
