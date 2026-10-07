"""DocumentVectorStore.get — 실제 pgvector 로 잰다(fullstack).

왜 실엔진인가. 이 조회는 SQL 이 전부다(`embd_vctr_cn::text` 파싱 · soft delete 제외 · 모델명 함께 반환).
SQLite 는 vector 타입이 없어 흉내 낼 수 없고, 흉내 낸 가짜로 통과시키면 배포에서 안 되는 채로 초록이 된다
(test_document_vector_index.py 머리말과 같은 이유). 규정 참고 표시의 서비스 시험은 이 저장소를 가짜로 바꿔
쓰므로(test_regulation_service_db.py) 진짜 SQL 은 여기서만 돈다.

저장소가 자기 연결로 읽고 쓰므로(ORM 세션 밖) 시험 데이터는 **커밋**되고, 끝에서 하드 삭제로 치운다
(FK CASCADE 가 벡터를 함께 지운다).
"""

from __future__ import annotations

import math
import uuid

import pytest
from sqlalchemy import text

from koipa.adapters.vectorstore.document_vectors import EMBED_DIM, DocumentVector, DocumentVectorStore
from koipa.db import engine

pytestmark = pytest.mark.fullstack


def _require_postgres() -> None:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
            has = conn.execute(text("SELECT to_regclass('tad_dm_doc_vctr_mng')")).scalar()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"Postgres not reachable: {type(exc).__name__}")
    if has is None:
        pytest.skip("tad_dm_doc_vctr_mng 없음 — pgvector 마이그레이션이 적용된 DB 가 필요하다")


@pytest.fixture
def doc_id():
    _require_postgres()
    with engine.begin() as conn:
        did = conn.execute(
            text("INSERT INTO tad_dm_doc_mng (file_nm, orgnl_frmat_nm) VALUES (:f, 'txt') RETURNING doc_id"),
            {"f": f"vecget-{uuid.uuid4()}"},
        ).scalar_one()
    try:
        yield str(did)
    finally:
        with engine.begin() as conn:
            conn.execute(text("DELETE FROM tad_dm_doc_mng WHERE doc_id = :d"), {"d": did})


def _unit(seed: int) -> list[float]:
    v = [math.sin(seed + i * 0.37) for i in range(EMBED_DIM)]
    n = math.sqrt(sum(x * x for x in v))
    return [x / n for x in v]


def test_returns_none_before_the_document_is_indexed(doc_id):
    assert DocumentVectorStore().get(doc_id) is None


def test_returns_the_stored_vector_and_the_model_that_made_it(doc_id):
    store = DocumentVectorStore()
    vec = _unit(1)
    store.upsert(doc_id=doc_id, embedding=vec, model="nlpai-lab/KURE-v1", chunk_count=3)

    got = store.get(doc_id)
    assert isinstance(got, DocumentVector)
    assert got.doc_id == doc_id
    assert got.model == "nlpai-lab/KURE-v1"                       # 질의 벡터를 만든 모델 — 규정 벡터와 같아야 비교한다
    assert len(got.embedding) == EMBED_DIM
    # pgvector 는 float32 로 저장한다 — 왕복 오차만 허용한다
    assert max(abs(a - b) for a, b in zip(got.embedding, vec)) < 1e-6


def test_upsert_again_replaces_the_vector(doc_id):
    store = DocumentVectorStore()
    store.upsert(doc_id=doc_id, embedding=_unit(1), model="m1")
    store.upsert(doc_id=doc_id, embedding=_unit(2), model="m2")
    got = store.get(doc_id)
    assert got.model == "m2"
    assert max(abs(a - b) for a, b in zip(got.embedding, _unit(2))) < 1e-6


def test_a_soft_deleted_document_has_no_vector_for_us(doc_id):
    """지운 문서의 벡터가 규정 조회에 쓰이면 안 된다 — soft delete 는 FK CASCADE 가 잡지 못하니 조인으로 거른다."""
    store = DocumentVectorStore()
    store.upsert(doc_id=doc_id, embedding=_unit(3), model="m")
    assert store.get(doc_id) is not None
    with engine.begin() as conn:
        conn.execute(text("UPDATE tad_dm_doc_mng SET del_dt = now() WHERE doc_id = :d"), {"d": doc_id})
    assert store.get(doc_id) is None


def test_an_unknown_document_is_none_not_an_error():
    _require_postgres()
    assert DocumentVectorStore().get(str(uuid.uuid4())) is None
