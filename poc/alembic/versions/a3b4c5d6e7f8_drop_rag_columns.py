"""유사문서 조회(RAG) 폐기 — 안 쓰게 된 칼럼 9개와 벡터 저장소 표 2개를 뺀다.

왜. 이 시스템은 등급 판정을 룰 키워드 argmax + 분류기로 완결한다 — 벡터 검색은 등급
결정에 관여하지 않는다(대신 도입됐던 것은 (1) 분류 요청 시 opt-in RAG 컨텍스트를
참고자료로 붙이는 것과 (2) 업로드 문서를 벡터스토어에 색인해 별도 질의응답(/answer)
에 쓰는 것 두 갈래였다). 둘 다 요건(FUN/NFR) 이 아니고, 정본 요구사항 추적표에는
RAG 관련 항목이 없다 — 유일하게 이름을 댄 "FUN-002"(가이드 문서 RAG 인덱싱)는
실제 RTM 어디에도 존재하지 않는 번호였다.

같은 커밋에서 소스(poc/src/koipa/rag/·adapters/vectorstore/·classify_service.py 의
use_rag 분기·guide_service.py 의 RagIndexer 의존)를 함께 걷었다. 이 판은 그 결과로
안 쓰게 된 칼럼만 뺀다.

    tb_classifications           rag_used · rag_top_k
    tb_classification_evidence   rag_ref_doc_id · rag_similarity
    tb_guides                    indexed · embedding_vector_count · index_name ·
                                  alias · model

⚠ tb_guides 데이터 유실 고지. 로컬 시험 DB 실측(2026-09) — 31행 전부 이 칼럼에 실값이
있었다(indexed=true 27행, index_name/alias/model 27~31행 비NULL). 벡터 색인 결과였고
색인 기능 자체가 없어지므로 값을 보존할 대상이 없다 — 문서 자체(guide_id·version·
filename 등)는 그대로 남는다. tb_classifications·tb_classification_evidence 는
실측 0행이라 유실 없음.

표 2개도 같은 판에서 DROP 한다.

    tb_rag_vectors               벡터 저장소(pgvector) — 임베딩·본문·payload
    tb_rag_aliases               컬렉션 별칭

두 표는 ORM 매핑이 아니라 raw SQL 판(a1b2c3d4e5f6)이 만든 것이라 models.py 에는
정의가 없다. 그래서 MariaDB 계열(f2a3b4c5d6e7, Base.metadata.create_all)에는 애초에
생기지 않는다 — 이 판은 PostgreSQL 계열 전용이다.

로컬 시험 DB 실측(2026-09): 두 표 모두 **0행**이라 유실 없음. 색인 경로(업로드 문서
RAG 색인·가이드 색인)를 같은 판에서 걷었으므로 다시 채워질 자리도 없다.

Revision ID: a3b4c5d6e7f8
Revises: e1f2a3b4c5d6
Create Date: 2026-09-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a3b4c5d6e7f8"
down_revision = "e1f2a3b4c5d6"
branch_labels = None
depends_on = None

# (표, 칼럼, 되돌릴 때 쓸 타입) — models.py 에서 뺀 9개와 1:1 이다.
COLUMNS: list[tuple[str, str, sa.types.TypeEngine]] = [
    ("tb_classifications", "rag_used", sa.Boolean()),
    ("tb_classifications", "rag_top_k", sa.SmallInteger()),
    ("tb_classification_evidence", "rag_ref_doc_id", sa.dialects.postgresql.UUID(as_uuid=True)),
    ("tb_classification_evidence", "rag_similarity", sa.Numeric(4, 3)),
    ("tb_guides", "indexed", sa.Boolean()),
    ("tb_guides", "embedding_vector_count", sa.Integer()),
    ("tb_guides", "index_name", sa.String(300)),
    ("tb_guides", "alias", sa.String(300)),
    ("tb_guides", "model", sa.String(100)),
]


# ORM 매핑이 아닌 raw SQL 표 — 같은 판에서 DROP 한다(위 docstring 참조).
TABLES = ["tb_rag_aliases", "tb_rag_vectors"]  # FK 방향상 aliases 를 먼저 지운다


def _has_column(conn, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "select 1 from information_schema.columns "
                "where table_schema = 'public' and table_name = :t and column_name = :c"
            ),
            {"t": table, "c": column},
        ).first()
    )


def upgrade() -> None:
    conn = op.get_bind()
    for table, column, _type in COLUMNS:
        if _has_column(conn, table, column):
            op.drop_column(table, column)
    for table in TABLES:
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table} CASCADE"))


def downgrade() -> None:
    """칼럼만 되돌린다 — 표 2개는 되돌리지 않는다.

    두 표의 DDL(pgvector 타입·HNSW 인덱스·tsvector 트리거)은 a1b2c3d4e5f6 판이
    가지고 있다. 여기서 손으로 다시 옮겨 적으면 그 판과 어긋날 자리가 생긴다 —
    되살릴 일이 생기면 그 판의 upgrade() 를 근거로 새 판을 쓴다.
    """
    conn = op.get_bind()
    for table, column, col_type in COLUMNS:
        if not _has_column(conn, table, column):
            op.add_column(table, sa.Column(column, col_type))
