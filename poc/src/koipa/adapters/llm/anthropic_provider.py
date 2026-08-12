"""Anthropic Claude provider. API 키 없으면 init에서 실패."""

from __future__ import annotations

import logging
import random
import time

from koipa.adapters.llm.base import LLMResponse, UsageRecord, estimate_cost_usd
from koipa.config import settings

logger = logging.getLogger(__name__)

# 재시도 가능한 오류 분류 — 429/5xx/타임아웃/일시 네트워크.
# anthropic 패키지를 import-time에 강결합하지 않기 위해 예외 객체를 introspection으로
# 분류한다(status_code 속성 + 클래스명 휴리스틱). 패키지 미설치·테스트 환경에서도 동작.
_RETRYABLE_EXC_NAMES = frozenset(
    {
        "RateLimitError",          # 429
        "InternalServerError",     # 5xx
        "APITimeoutError",         # 요청 타임아웃
        "APIConnectionError",      # 일시 네트워크
        "OverloadedError",         # 529
    }
)


def _is_retryable(exc: BaseException) -> bool:
    """429/5xx/타임아웃/연결 오류면 True. 4xx(429 제외) 등 항구적 오류는 False."""
    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        # 429(rate limit) 또는 5xx(서버측 일시 오류)만 재시도. 그 외 4xx는 항구적.
        return status == 429 or status >= 500
    # status_code 없는 예외(타임아웃·연결 등)는 클래스명으로 판별.
    return type(exc).__name__ in _RETRYABLE_EXC_NAMES


class AnthropicProvider:
    name = "anthropic"

    # 지수 백오프 기본값 — settings에 값이 있으면 우선.
    _DEFAULT_MAX_RETRIES = 3
    _DEFAULT_BASE_DELAY = 0.5   # 초
    _DEFAULT_MAX_DELAY = 8.0    # 초

    def __init__(self, *, model: str | None = None, api_key: str | None = None) -> None:
        from anthropic import Anthropic

        key = api_key or settings.anthropic_api_key
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self._client = Anthropic(api_key=key)
        self.model = model or settings.llm_model or "claude-sonnet-4-6"
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
        kwargs: dict = {
            "model": self.model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        last_exc: BaseException | None = None
        # 0회차는 본 호출, 이후 _max_retries회까지 재시도(총 1+_max_retries 시도).
        for attempt in range(self._max_retries + 1):
            try:
                msg = self._client.messages.create(**kwargs)
                text = "".join(
                    block.text for block in msg.content if getattr(block, "type", "") == "text"
                )
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
                last_exc = exc
                retryable = _is_retryable(exc)
                if retryable and attempt < self._max_retries:
                    # full-jitter 지수 백오프(min(base*2^n, cap) 범위에서 랜덤).
                    cap = min(self._base_delay * (2 ** attempt), self._max_delay)
                    delay = random.uniform(0.0, cap)
                    logger.warning(
                        "anthropic call failed (%s) — retry %d/%d after %.2fs",
                        type(exc).__name__, attempt + 1, self._max_retries, delay,
                    )
                    time.sleep(delay)
                    continue
                # 비재시도성이거나 재시도 소진 — 가시화하고 실패 반환.
                logger.warning(
                    "anthropic call failed (%s) after %d attempt(s), giving up: %s",
                    type(exc).__name__, attempt + 1, exc,
                )
                break

        # 루프 탈출 = 실패. 호출부가 무시 못 하게 success=False + error_code로 가시화.
        assert last_exc is not None  # 루프는 성공 시 return, 실패 시에만 여기 도달
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
                error_code=type(last_exc).__name__,
            ),
            meta={"error": str(last_exc), "retries_exhausted": _is_retryable(last_exc)},
        )

    def count_tokens(self, text: str) -> int:
        return max(1, len(text) // 3)
