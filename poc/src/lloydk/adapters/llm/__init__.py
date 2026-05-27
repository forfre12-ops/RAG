"""LLM Provider Adapter — 원격·로컬 자유 선택.

provider 이름:
- noop          : 결정론적 mock (CI·dryrun)
- anthropic     : Claude 원격 API
- openai        : OpenAI 원격 API
- local_openai  : OpenAI 호환 로컬 endpoint (vLLM·Ollama·LM Studio·llama.cpp 공통)
- vllm          : (alias) local_openai
- ollama        : (alias) local_openai + Ollama 기본 endpoint·api_key

납품 일반화: 같은 코드에서 settings.llm_provider 또는 build_provider(name=...) 호출만으로
원격/로컬 어느 쪽이든 전환 가능. GPU 미보유 환경엔 원격, 보유 환경엔 로컬.
"""

from __future__ import annotations

from lloydk.adapters.llm.base import LLMProvider, LLMResponse, UsageRecord
from lloydk.adapters.llm.noop_provider import NoopProvider

__all__ = ["LLMProvider", "LLMResponse", "UsageRecord", "NoopProvider", "build_provider"]


def build_provider(name: str | None = None) -> LLMProvider:
    """settings.llm_provider 또는 인자로 지정한 provider 인스턴스 반환.

    원격/로컬 양쪽 모두 동일 인터페이스로 호출 가능.
    """
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
    if chosen in ("local_openai", "vllm", "ollama", "lm_studio"):
        # 모든 OpenAI 호환 로컬 endpoint는 LocalOpenAIProvider 단일 어댑터로 처리.
        # provider_label로 telemetry에 구분 가능.
        from lloydk.adapters.llm.local_openai_provider import LocalOpenAIProvider

        # Ollama는 기본 base_url/api_key가 다르므로 alias별 디폴트 주입.
        if chosen == "ollama":
            return LocalOpenAIProvider(
                base_url="http://localhost:11434/v1",
                api_key="ollama",
                provider_label="ollama",
            )
        if chosen == "lm_studio":
            return LocalOpenAIProvider(
                base_url="http://localhost:1234/v1",
                api_key="lm-studio",
                provider_label="lm_studio",
            )
        # vllm·local_openai: settings.local_llm_* 기본 사용
        return LocalOpenAIProvider(provider_label=chosen)

    raise ValueError(f"unknown LLM provider: {chosen}")
