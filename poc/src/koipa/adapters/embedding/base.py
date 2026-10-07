"""Embedding 추상 인터페이스."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, runtime_checkable


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    dim: int
    model: str


@runtime_checkable
class EmbeddingProvider(Protocol):
    name: str
    dim: int

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        ...
