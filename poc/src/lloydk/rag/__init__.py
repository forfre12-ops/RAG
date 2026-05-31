"""lloydk.rag — 도메인 독립 RAG 코어 서브패키지.

다른 프로젝트에서 재사용할 수 있도록 분류(영업비밀) 도메인 로직과 분리됩니다.

Public API:
    from lloydk.rag import RagIndexer, IndexResult
    from lloydk.rag import expand_then_search
    from lloydk.rag import expand_rule, expand_llm, expand_hybrid, QueryExpansion
    from lloydk.rag import synthesize_answer, build_answer_prompt

기존 import 경로는 모두 그대로 동작합니다 (backward compat shim).
"""

from lloydk.rag.indexer import IndexResult, RagIndexer
from lloydk.rag.query_expansion import (
    QueryExpansion,
    expand_hybrid,
    expand_llm,
    expand_rule,
)
from lloydk.rag.retrieval import expand_then_search
from lloydk.rag.answer import build_answer_prompt, synthesize_answer

__all__ = [
    "RagIndexer",
    "IndexResult",
    "expand_then_search",
    "QueryExpansion",
    "expand_rule",
    "expand_llm",
    "expand_hybrid",
    "synthesize_answer",
    "build_answer_prompt",
]
