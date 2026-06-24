"""PgVectorStore 단위 테스트 — 라이브 PG 불요(순수 헬퍼 + Protocol 적합성).

실 PG 통합(upsert/search/search_hybrid against pgvector)은 의사결정_대장 §03 게이트
통과 후 docker-compose.pgvector.yml 스택에서 scripts/revalidate_pg_lexical.py 로 검증.
"""
from __future__ import annotations

import pytest

from lloydk.adapters.vectorstore.base import HybridVectorStore, VectorStore
from lloydk.adapters.vectorstore.pg_store import PgVectorStore


def test_vec_lit_format():
    assert PgVectorStore._vec_lit([0.1, 0.2, 0.3]) == "[0.1,0.2,0.3]"
    assert PgVectorStore._vec_lit([]) == "[]"
    assert PgVectorStore._vec_lit([1, 2]) == "[1.0,2.0]"  # 정수도 float 직렬화


def test_bigram_korean_and_ascii():
    out = PgVectorStore._bigram("영업비밀 VRF compressor 35%").split()
    # ASCII 단어 보존(소문자)
    assert "vrf" in out and "compressor" in out and "35" in out
    # 한글 2-gram (pg_bigm 근사)
    assert "영업" in out and "업비" in out and "비밀" in out
    # 1글자 한글은 그대로, 빈 입력은 빈 문자열
    assert PgVectorStore._bigram("가") == "가"
    assert PgVectorStore._bigram("") == ""
    assert PgVectorStore._bigram(None) == ""


def test_filter_sql_column_vs_payload():
    store = PgVectorStore(engine=object())  # 헬퍼만 검증 — engine 미사용
    params: dict = {}
    clause = store._filter_sql({"tenant_id": "acme", "doc_type": "legal"}, params)
    assert "tenant_id = :f0" in clause                 # 전용 컬럼은 직접 비교
    assert "payload ->> 'doc_type' = :f1" in clause    # 그 외는 payload JSONB
    assert params["f0"] == "acme" and params["f1"] == "legal"
    assert store._filter_sql(None, {}) == ""           # 빈 필터는 빈 절


def test_protocol_conformance():
    store = PgVectorStore(engine=object())
    assert isinstance(store, VectorStore)
    assert isinstance(store, HybridVectorStore)   # 진짜 하이브리드 마커 충족
    assert store.name == "postgres"


def test_ensure_collection_dim_guard():
    store = PgVectorStore(engine=object())
    store.ensure_collection("c", 1024)            # vector(1024)와 일치 → ok
    with pytest.raises(ValueError):
        store.ensure_collection("c", 768)         # 차원 불일치 → fail-fast
