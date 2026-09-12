"""RAG 벡터 스키마 컬럼 주석 — tb_rag_vectors / tb_rag_aliases COMMENT ON.

Revision ID: a7b8c9d0e1f2
Revises: c3d4e5f6a7b8
Create Date: 2026-07-08

tb_rag_vectors 는 a1b2c3d4e5f6 에서 raw SQL 로 생성돼 ORM(db/models.py) 밖에 있어 컬럼
의미·설계근거가 스키마에 남아있지 않았다. 이 마이그레이션이 각 컬럼/테이블에 설명을
DB 자체에 영구 부착한다(psql \\d+ · information_schema.col_description · 모든 DB 툴 노출).
비파괴 — COMMENT 만 추가(구조/데이터 불변). 재실행 안전(COMMENT 는 덮어쓰기).
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "c3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_TABLE_COMMENTS = {
    "tb_rag_vectors": (
        "RAG 하이브리드 벡터스토어 — pgvector dense(HNSW 코사인) + bigram-tsvector "
        "ts_rank(어휘). collection 컬럼으로 네임스페이스(ES index 대체). 생성=alembic a1b2c3d4e5f6"
    ),
    "tb_rag_aliases": (
        "alias→collection 매핑. 무중단 재색인 blue/green 스왑(ES alias swap 대체)"
    ),
}

_VECTOR_COLS = {
    "collection": "논리 네임스페이스(ES index 대체). 한 테이블에 여러 컬렉션 공존. PK 1번째",
    "id": "벡터 항목 고유 ID(컬렉션 내 유일, 보통 <doc_id>#<chunk_idx>). PK 2번째",
    "doc_id": "원문 문서 식별자(느슨한 참조·FK 아님). 문서 단위 삭제·필터용",
    "chunk_idx": "문서 내 청크 순번(0-base). 원문 재구성·인접 청크 조회",
    "content": "청크 원문 텍스트. 결과 반환 + pg_bigm 후보생성 대상",
    "bigram_text": (
        "content 의 bigram(2-gram) 토큰(앱 PgVectorStore._bigram 이 채움). 한국어 어휘채점"
        "(ts_rank) 소스 — 어절 대신 bigram 이라 조사·굴절에 강함"
    ),
    "embedding": "dense 임베딩 vector(1024) (KURE-v1/BGE-M3, 차원 고정·가변불가). HNSW 코사인 kNN 대상",
    "tsv": (
        "어휘검색 tsvector. bigram_text 에서 DB 자동생성(GENERATED STORED). content 아닌 "
        "bigram 사용: 어절 NL R@5 47% → bigram 87%(≈nori-BM25 89%)"
    ),
    "payload": "임의 메타데이터 JSONB(heading_path·source·등급 등). 필터·표시용",
    "created_at": "적재 시각. 재색인·TTL·감사용",
}

_ALIAS_COLS = {
    "alias": "논리 이름(예: uploads). 앱이 참조하는 안정적 이름",
    "collection": "실제 물리 collection(예: uploads_v3). 재색인 시 이 값만 교체",
    "updated_at": "alias→collection 갱신 시각",
}


def _esc(s: str) -> str:
    return s.replace("'", "''")


def upgrade() -> None:
    for tbl, comment in _TABLE_COMMENTS.items():
        op.execute(f"COMMENT ON TABLE {tbl} IS '{_esc(comment)}'")
    for col, comment in _VECTOR_COLS.items():
        op.execute(f"COMMENT ON COLUMN tb_rag_vectors.{col} IS '{_esc(comment)}'")
    for col, comment in _ALIAS_COLS.items():
        op.execute(f"COMMENT ON COLUMN tb_rag_aliases.{col} IS '{_esc(comment)}'")


def downgrade() -> None:
    for tbl in _TABLE_COMMENTS:
        op.execute(f"COMMENT ON TABLE {tbl} IS NULL")
    for col in _VECTOR_COLS:
        op.execute(f"COMMENT ON COLUMN tb_rag_vectors.{col} IS NULL")
    for col in _ALIAS_COLS:
        op.execute(f"COMMENT ON COLUMN tb_rag_aliases.{col} IS NULL")
