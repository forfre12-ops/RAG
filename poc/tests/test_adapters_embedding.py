"""Embedding 어댑터 (드라이런 백엔드).

[2026-09] 벡터스토어(InMemoryStore) 시험은 유사문서 검색 폐기와 함께 걷었다.
"""

from __future__ import annotations

import math

from koipa.adapters.embedding import HashEmbedding


def _cos(a: list[float], b: list[float]) -> float:
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(x * x for x in b)) or 1.0
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def test_hash_embedding_deterministic_and_unit_norm():
    emb = HashEmbedding(dim=128)
    a, b = emb.embed(["특급기밀 신제품 설계도", "특급기밀 신제품 설계도"]).vectors
    assert _cos(a, b) > 0.999
    # L2 정규화
    norm = math.sqrt(sum(x * x for x in a))
    assert abs(norm - 1.0) < 1e-6


def test_hash_embedding_distinguishes_different_text():
    emb = HashEmbedding(dim=512)
    a, b = emb.embed(["특급기밀 M&A 계획", "회사 점심메뉴 공지"]).vectors
    assert _cos(a, b) < 0.5  # 서로 다른 텍스트는 유사도 낮음
