"""Embedding + Vector store 어댑터 (드라이런 백엔드)."""

from __future__ import annotations

import math

from koipa.adapters.embedding import HashEmbedding
from koipa.adapters.vectorstore import InMemoryStore


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


def test_inmemory_store_search_with_filter():
    emb = HashEmbedding(dim=64)
    vecs = emb.embed(["TS 자료", "S1 자료", "S3 공지"]).vectors

    vs = InMemoryStore()
    vs.ensure_collection("t", dim=64)
    n = vs.upsert(
        "t",
        ["d1", "d2", "d3"],
        vecs,
        [{"grade": "TS"}, {"grade": "S1"}, {"grade": "S3"}],
    )
    assert n == 3
    assert vs.count("t") == 3

    q = emb.embed(["TS 자료"]).vectors[0]
    hits = vs.search("t", q, top_k=2)
    assert hits[0].id == "d1"

    # 필터로 TS만 검색
    only_ts = vs.search("t", q, top_k=5, filter={"grade": "TS"})
    assert {h.id for h in only_ts} == {"d1"}


def test_inmemory_store_upsert_overwrites():
    vs = InMemoryStore()
    vs.ensure_collection("c", dim=4)
    vs.upsert("c", ["x"], [[1.0, 0, 0, 0]], [{"v": 1}])
    vs.upsert("c", ["x"], [[0, 1.0, 0, 0]], [{"v": 2}])
    assert vs.count("c") == 1
    hit = vs.search("c", [0, 1, 0, 0], top_k=1)[0]
    assert hit.payload["v"] == 2
