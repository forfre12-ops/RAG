"""Anthropic Claude provider. API 키 없으면 init에서 실패."""

from __future__ import annotations

import time

from lloydk.adapters.llm.base import LLMResponse, UsageRecord, estimate_cost_usd
from lloydk.config import settings


class AnthropicProvider:
    name = "anthropic"

    def __init__(self, *, model: str | None = None, api_key: str | None = None) -> None:
        from anthropic import Anthropic

        key = api_key or settings.anthropic_api_key
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self._client = Anthropic(api_key=key)
        self.model = model or settings.llm_model or "claude-sonnet-4-6"

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> LLMResponse:
        start = time.perf_counter()
        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        try:
            msg = self._client.messages.create(**kwargs)
            text = "".join(block.text for block in msg.content if getattr(block, "type", "") == "text")
            in_tok = getattr(msg.usage, "input_tokens", 0)
            out_tok = getattr(msg.usage, "output_tokens", 0)
            return LLMResponse(
                text=text,
                usage=UsageRecord(
                    provider=self.name,
                    model=self.model,
                    input_tokens=in_tok,
                    output_tokens=out_tok,
                    cost_usd=estimate_cost_usd(self.model, in_tok, out_tok),
                    latency_ms=int((time.perf_counter() - start) * 1000),
                ),
            )
        except Exception as exc:  # noqa: BLE001
            return LLMResponse(
                text="",
                usage=UsageRecord(
                    provider=self.name,
                    model=self.model,
                    input_tokens=0,
                    output_tokens=0,
                    cost_usd=0.0,
                    latency_ms=int((time.perf_counter() - start) * 1000),
                    success=False,
                    error_code=type(exc).__name__,
                ),
                meta={"error": str(exc)},
            )

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 3)
