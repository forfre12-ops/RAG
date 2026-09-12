"""결정론적 해시 기반 임베딩 — 모델 다운로드 없이 PoC 파이프라인 검증용.

특성:
- 동일 텍스트 → 동일 벡터
- L2 정규화 (cosine similarity 가능)
- 토큰 빈도 기반 sparse-like 시그널 보존 → P2 비교 시 약한 baseline 역할
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Sequence

from koipa.adapters.embedding.base import EmbeddingResult


_TOKEN_RE = re.compile(r"[\w가-힣]+", re.UNICODE)


class HashEmbedding:
    name = "hash"

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    def _vec_for(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        tokens = _TOKEN_RE.findall(text) or [text[:32]]
        for tok in tokens:
            h = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
            idx = int.from_bytes(h[:4], "little") % self.dim
            sign = 1.0 if h[4] & 1 else -1.0
            vec[idx] += sign
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        return [x / norm for x in vec]

    def embed(self, texts: Sequence[str]) -> EmbeddingResult:
        vectors = [self._vec_for(t) for t in texts]
        return EmbeddingResult(vectors=vectors, dim=self.dim, model=self.name)
