"""OpenAI provider (GPT-4o)."""

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


# 작업 #18: OpenAI SDK 예외 기준 재시도 가능 분류.
# RateLimitError(429)/APITimeoutError/APIConnectionError + APIStatusError(status>=500).
# anthropic과 동일하게 패키지 강결합을 피하려 status 속성 + 클래스명 휴리스틱으로 판별.
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


class OpenAIProvider:
    name = "openai"

    # 지수 백오프 기본값 — settings에 값 있으면 우선(anthropic과 동일 정책).
    _DEFAULT_MAX_RETRIES = 3
    _DEFAULT_BASE_DELAY = 0.5
    _DEFAULT_MAX_DELAY = 8.0

    def __init__(self, *, model: str | None = None, api_key: str | None = None) -> None:
        from openai import OpenAI

        key = api_key or settings.openai_api_key
        if not key:
            raise RuntimeError("OPENAI_API_KEY not set")
        self._client = OpenAI(api_key=key)
        self.model = model or "gpt-4o"
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
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
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
            in_tok = resp.usage.prompt_tokens
            out_tok = resp.usage.completion_tokens
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
