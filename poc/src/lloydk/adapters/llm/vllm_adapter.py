from __future__ import annotations
from lloydk.config import settings
from .openai_adapter import OpenAIAdapter


class VLLMAdapter(OpenAIAdapter):
    """
    vLLM exposes OpenAI-compatible API.
    Qwen3 default. Supports thinking/non-thinking mode via chat_template_kwargs.
      - settings.vllm_enable_thinking=False -> fast generation (recommended for FUN-003 synthesis)
      - True  -> reasoning mode (recommended for boundary judgement / FUN-024 evaluation)
    Per-call override: pass thinking=True|False to complete().
    Requires vLLM >= 0.8.5 for Qwen3.
    """
    name = "vllm"

    def __init__(self):
        super().__init__(
            model=settings.vllm_model,
            base_url=settings.vllm_base_url,
            api_key="EMPTY",
        )
        self._default_thinking = settings.vllm_enable_thinking

    def complete(self, system: str, user: str, **opts) -> str:
        thinking = opts.pop("thinking", self._default_thinking)
        resp = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=opts.get("temperature", 0.6 if thinking else 0.7),
            top_p=opts.get("top_p", 0.95 if thinking else 0.8),
            max_tokens=opts.get("max_tokens", 4000),
            extra_body={"chat_template_kwargs": {"enable_thinking": bool(thinking)}},
        )
        return resp.choices[0].message.content or ""
