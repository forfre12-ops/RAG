"""vLLM OpenAI 호환 endpoint provider (Qwen3-14B 등 OSS 모델)."""

from __future__ import annotations

import logging
import time

from lloydk.adapters.llm.base import (
    LLMResponse,
    UsageRecord,
    estimate_cost_usd,
    retry_with_backoff,
)
from lloydk.config import settings

logger = logging.getLogger(__name__)


# 작업 #18: vLLM은 OpenAI SDK 사용 — OpenAI 예외 기준 재시도 분류.
# RateLimitError(429)/APITimeoutError/APIConnectionError + status>=500.
_RETRYABLE_EXC_NAMES = frozenset(
    {
        "RateLimitError",        # 429
        "APITimeoutError",       # 요청 타임아웃
        "APIConnectionError",    # 일시 네트워크
        "InternalServerError",   # 5xx
        "APIConnectionTimeoutError",
    }
)


def _is_retryable(exc: BaseException) -> bool:
    """429/5xx/타임아웃/연결 오류면 True. 그 외 4xx 등 항구적 오류는 False."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        return status == 429 or status >= 500
    return type(exc).__name__ in _RETRYABLE_EXC_NAMES


class VLLMProvider:
    name = "vllm"

    # 지수 백오프 기본값 — settings에 값 있으면 우선.
    _DEFAULT_MAX_RETRIES = 3
    _DEFAULT_BASE_DELAY = 0.5
    _DEFAULT_MAX_DELAY = 8.0

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
        self._max_retries = int(getattr(settings, "llm_max_retries", self._DEFAULT_MAX_RETRIES))
        self._base_delay = float(getattr(settings, "llm_retry_base_delay", self._DEFAULT_BASE_DELAY))
        self._max_delay = float(getattr(settings, "llm_retry_max_delay", self._DEFAULT_MAX_DELAY))

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
            # 작업 #18: 429/5xx/타임아웃/연결 오류에 full-jitter 지수 백오프 재시도.
            resp = retry_with_backoff(
                lambda: self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                ),
                is_retryable=_is_retryable,
                max_retries=self._max_retries,
                base_delay=self._base_delay,
                max_delay=self._max_delay,
                label=self.name,
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
