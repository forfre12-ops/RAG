"""아무도 읽지 않는 칼럼 10개를 뺀다.

왜. `scripts/audit_unused.py` 가 칼럼 188 개 중 models.py 밖 참조가 0 인 것 16 개를
집어냈다. 하나씩 열어 셋으로 갈랐다.

    오검출 4   evidence_id · usage_id (기본키 Identity) · labeled_at · logged_at
               (server_default=now()). DB 가 채우므로 코드가 이름으로 안 부르는 것이 정상이다.
    보류 2     split_method(6/6) · model_type(9/9). 기본값으로 **실제 값이 들어 있다.**
               읽는 코드는 없지만 지우면 기존 행의 값을 버린다. model_type 은
               classifier/reranker 구분자라 reranker 도입 시 필요하다.
    제거 10    아래 목록. 223 실 데이터에서 **전부 NULL** 이었다(행은 있는데 이 칼럼만 비었다).

영향도 전수 확인(2026-08-29).

    src(models.py 제외)   0        tests            0
    scripts               0        Pydantic 스키마   0
    OpenAPI 규약서         0        alembic          1 = 베이스라인 CREATE TABLE

즉 어떤 코드도 읽거나 쓰지 않는다. 배포된 기능이 이 칼럼에 의존하지 않는다.

⚠ 제출본 표기가 바뀐다. 「테이블정의서·ERD」·회신서·색인의 **249 칼럼 -> 239** 를 함께
고쳐야 한다(ORM 매핑 236 -> 226 + 검색용 표 2종 13). 문서 수정은 별건 커밋이다.

Revision ID: d1a2b3c4e5f7
Revises: c9e1f2a3b4d5
Create Date: 2026-08-29
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d1a2b3c4e5f7"
down_revision = "c9e1f2a3b4d5"
branch_labels = None
depends_on = None

# (표, 칼럼, 되돌릴 때 쓸 타입) — models.py 에서 뺀 10 개와 1:1 이다.
COLUMNS: list[tuple[str, str, sa.types.TypeEngine]] = [
    ("tb_level_keywords", "example_context", sa.Text()),
    ("tb_chunks", "page_start", sa.SmallInteger()),
    ("tb_chunks", "page_end", sa.SmallInteger()),
    ("tb_classifications", "rag_agreement", sa.Boolean()),
    ("tb_classification_evidence", "attention_scores", sa.dialects.postgresql.JSONB()),
    ("tb_model_versions", "model_size_mb", sa.Integer()),
    ("tb_training_runs", "gpu_info", sa.dialects.postgresql.JSONB()),
    ("tb_prompt_versions", "avg_quality_score", sa.Numeric(3, 2)),
    ("tb_prompt_versions", "usage_count", sa.Integer()),
    ("tb_prompt_versions", "approval_rate", sa.Numeric(3, 2)),
]


def _has_column(conn, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "select 1 from information_schema.columns "
                "where table_schema = 'public' and table_name = :t and column_name = :c"
            ),
            {"t": table, "c": column},
        ).scalar()
    )


def upgrade() -> None:
    conn = op.get_bind()
    for table, column, _type in COLUMNS:
        if conn.execute(sa.text("select to_regclass(:t)"), {"t": table}).scalar() is None:
            continue
        if not _has_column(conn, table, column):
            continue  # 재실행 안전
        op.drop_column(table, column)


def downgrade() -> None:
    """되돌리면 칼럼은 돌아오지만 **값은 돌아오지 않는다**(전부 NULL 이었으므로 손실 없음)."""
    conn = op.get_bind()
    for table, column, type_ in COLUMNS:
        if conn.execute(sa.text("select to_regclass(:t)"), {"t": table}).scalar() is None:
            continue
        if _has_column(conn, table, column):
            continue
        op.add_column(table, sa.Column(column, type_, nullable=True))
