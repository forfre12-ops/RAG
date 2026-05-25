"""LLM Provider Adapter — Anthropic/OpenAI/vLLM/Noop 교체 가능 인터페이스."""

from __future__ import annotations

from lloydk.adapters.llm.base import LLMProvider, LLMResponse, UsageRecord
from lloydk.adapters.llm.noop_provider import NoopProvider

__all__ = ["LLMProvider", "LLMResponse", "UsageRecord", "NoopProvider", "build_provider"]


def build_provider(name: str | None = None) -> LLMProvider:
    """settings.llm_provider 또는 인자로 지정한 provider 인스턴스 반환."""
    from lloydk.config import settings

    chosen = (name or settings.llm_provider or "noop").lower()
    if chosen == "noop":
        return NoopProvider()
    if chosen == "anthropic":
        from lloydk.adapters.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    if chosen == "openai":
        from lloydk.adapters.llm.openai_provider import OpenAIProvider

        return OpenAIProvider()
    if chosen == "vllm":
        from lloydk.adapters.llm.vllm_provider import VLLMProvider

        return VLLMProvider()
    raise ValueError(f"unknown LLM provider: {chosen}")
