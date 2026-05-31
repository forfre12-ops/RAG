"""backward compat shim — 실제 구현은 lloydk.rag.query_expansion으로 이동됨."""
from lloydk.rag.query_expansion import (  # noqa: F401
    QueryExpansion,
    _DEFAULT_SYNONYMS,
    expand_hybrid,
    expand_llm,
    expand_rule,
)

# 하위호환 alias
_RULE_SYNONYMS = _DEFAULT_SYNONYMS

__all__ = ["QueryExpansion", "expand_rule", "expand_llm", "expand_hybrid"]
