"""LLM Provider 추상 인터페이스 + 비용 추정."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable


@dataclass
class UsageRecord:
    """LLM 호출 1건의 토큰/비용 기록. DB `llm_usage` 적재용."""

    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: int = 0
    success: bool = True
    error_code: str | None = None


@dataclass
class LLMResponse:
    text: str
    usage: UsageRecord
    meta: dict = field(default_factory=dict)


# USD per 1M tokens, 2026-05 시점 공개 단가.
PRICE_TABLE: dict[str, tuple[float, float]] = {
    "claude-sonnet-4-6": (3.0, 15.0),
    "claude-opus-4-7": (15.0, 75.0),
    "claude-haiku-4-5": (1.0, 5.0),
    "gpt-4o": (2.5, 10.0),
    "gpt-4o-mini": (0.15, 0.6),
    "gemini-2.5-pro": (1.25, 10.0),
    "Qwen/Qwen3-14B": (0.0, 0.0),
    "noop": (0.0, 0.0),
}


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    in_price, out_price = PRICE_TABLE.get(model, (0.0, 0.0))
    return (input_tokens / 1_000_000) * in_price + (output_tokens / 1_000_000) * out_price


@runtime_checkable
class LLMProvider(Protocol):
    name: str
    model: str

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        max_tokens: int = 1024,
        temperature: float = 0.7,
    ) -> LLMResponse:
        ...

    def count_tokens(self, text: str) -> int:
        ...
