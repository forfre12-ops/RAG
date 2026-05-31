"""backward compat shim — 실제 구현은 lloydk.rag.answer로 이동됨."""
from lloydk.rag.answer import (  # noqa: F401
    _DEFAULT_GRADE_DESCRIPTIONS,
    _deterministic_answer,
    build_answer_prompt,
    synthesize_answer,
)

__all__ = ["synthesize_answer", "build_answer_prompt"]
