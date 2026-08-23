"""backward compat shim — 실제 구현은 koipa.rag.indexer로 이동됨."""
from koipa.rag.indexer import (  # noqa: F401
    IndexResult,
    RagIndexer,
    _safe_token,
)

__all__ = ["RagIndexer", "IndexResult"]
