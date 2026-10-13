"""idx_mv_active 를 MariaDB 이식 가능한 생성 칼럼 방식으로 바꾼다.

왜. 로컬 개발 DB 를 MariaDB 로 전환하는 작업 중 발견했다. `idx_mv_active` 는
"활성 모델 버전은 항상 1개" 불변식을 `postgresql_where=is_active=TRUE` 부분
유니크 인덱스로 지키고 있었는데, **MariaDB 는 부분 인덱스(WHERE)가 없다.**

SQLAlchemy 는 `postgresql_where` 를 다른 dialect 에서 경고 없이 떨어뜨린다 —
실측(2026-09): MariaDB 에 그대로 create_all 하면 `UNIQUE KEY (is_active)` 가
조건 없이 생겨, **비활성 모델 버전도 동시에 2개를 못 넣는** 상태가 된다(is_active
값마다 유니크가 걸리므로). 오류 없이 조용히 깨지는 자리였다.

고침 — 생성 칼럼 `active_key`(활성일 때만 1, 아니면 NULL)에 유니크를 건다.
UNIQUE 인덱스는 NULL 을 여러 개 허용하므로 비활성 행은 몇 개든 공존하고 활성
행만 하나로 묶인다. `GENERATED ALWAYS AS (...) STORED` 는 Postgres 12+ 와
MariaDB 10.2+ 양쪽에 있고, 실측으로 두 엔진 모두 두 번째 활성 삽입에서
IntegrityError 가 나는 것을 확인했다(poc/src/koipa/db/models.py 의 같은 커밋).

이 판은 PostgreSQL 만 다룬다 — MariaDB 베이스라인은 별도 산출물이다(create_all
로 기동 확인만 마쳤고, alembic 이력을 아직 새로 잡지 않았다).

Revision ID: e1f2a3b4c5d6
Revises: d1a2b3c4e5f7
Create Date: 2026-09-04
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "e1f2a3b4c5d6"
down_revision = "d1a2b3c4e5f7"
branch_labels = None
depends_on = None


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
    if not _has_column(conn, "tb_model_versions", "active_key"):
        op.execute(
            "ALTER TABLE tb_model_versions "
            "ADD COLUMN active_key SMALLINT "
            "GENERATED ALWAYS AS (CASE WHEN is_active THEN 1 END) STORED"
        )
    op.execute("DROP INDEX IF EXISTS idx_mv_active")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_active "
        "ON tb_model_versions (active_key)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_mv_active")
    op.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_active "
        "ON tb_model_versions (is_active) WHERE (is_active = true)"
    )
    op.execute("ALTER TABLE tb_model_versions DROP COLUMN IF EXISTS active_key")
