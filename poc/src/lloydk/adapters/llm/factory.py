from __future__ import annotations
from lloydk.config import settings
from .base import LLMAdapter


def get_llm(provider: str | None = None) -> LLMAdapter:
    p = (provider or settings.llm_provider).lower()
    if p == "anthropic":
        from .anthropic_adapter import AnthropicAdapter
        return AnthropicAdapter()
    if p == "openai":
        from .openai_adapter import OpenAIAdapter
        return OpenAIAdapter(model=settings.llm_model or "gpt-4o")
    if p == "vllm":
        from .vllm_adapter import VLLMAdapter
        return VLLMAdapter()
    raise ValueError(f"unknown llm provider: {p}")
