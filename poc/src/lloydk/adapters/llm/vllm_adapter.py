from __future__ import annotations
from lloydk.config import settings
from .openai_adapter import OpenAIAdapter


class VLLMAdapter(OpenAIAdapter):
    """vLLM exposes OpenAI-compatible API."""
    name = "vllm"

    def __init__(self):
        super().__init__(
            model=settings.vllm_model,
            base_url=settings.vllm_base_url,
            api_key="EMPTY",
        )
