"""vLLM OpenAI 호환 endpoint provider (Qwen3-14B 등 OSS 모델)."""

from __future__ import annotations

import time

from lloydk.adapters.llm.base import LLMResponse, UsageRecord, estimate_cost_usd
from lloydk.config import settings


class VLLMProvider:
    name = "vllm"

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        enable_thinking: bool | None = None,
    ) -> None:
        from openai import OpenAI

        self._client = OpenAI(
            base_url=base_url or settings.vllm_base_url,
            api_key="EMPTY",
        )
        self.model = model or settings.vllm_model
        self.enable_thinking = (
            enable_thinking if enable_thinking is not None else settings.vllm_enable_thinking
        )

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> LLMResponse:
        start = time.perf_counter()
        # Qwen3 thinking 토글: 프롬프트 끝에 /think 또는 /no_think 추가
        directive = "/think" if self.enable_thinking else "/no_think"
        full_user = f"{prompt}\n\n{directive}"
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": full_user})

        try:
            resp = self._client.chat.completions.create(
                model=self.model,
                messages=messages,
                max_tokens=max_tokens,
                temperature=temperature,
            )
            text = resp.choices[0].message.content or ""
            in_tok = getattr(resp.usage, "prompt_tokens", 0)
            out_tok = getattr(resp.usage, "completion_tokens", 0)
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
                meta={"thinking": self.enable_thinking},
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
