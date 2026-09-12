"""정수 대리키 9개를 SERIAL 에서 표준 IDENTITY 로 바꾼다.

왜.

    SERIAL 은 표준 SQL 이 아니다. PostgreSQL 10 부터 표준은
    `GENERATED ... AS IDENTITY` 이고 SERIAL 은 남겨둔 옛 표기다.
    표기 문제로 그치지 않고 동작이 다르다.

    · SERIAL 은 `INSERT INTO t (id) VALUES (999)` 를 **막지 못한다.** 시퀀스는
      그대로 1 부터 올라가다 999 에 부딪혀 중복키로 터진다. 언제 터질지는
      데이터가 999 건 쌓이는 시점에 달려 있어 운영 중에 드러난다.
      IDENTITY ALWAYS 는 그 INSERT 자체를 거부한다.
    · SERIAL 은 시퀀스에 별도 USAGE 권한이 필요해 권한 관리가 표와 갈라진다.
      IDENTITY 는 칼럼에 속하므로 표 권한만 보면 된다.

ALWAYS 를 고른 근거. 코드 어디에서도 이 9 개 칼럼에 값을 직접 넣지 않는다
(services 의 ORM 생성자는 FK 만 채우고, baseline 씨딩 INSERT 도 칼럼 목록에서
빠져 있다). 그러므로 BY DEFAULT 로 느슨하게 둘 이유가 없다.

파티션 표 검증(223 · PostgreSQL 16.14 실측 2026-08-29). 파티션 부모에 IDENTITY 를
걸 수 있고, 자식 파티션으로 라우팅되는 INSERT 도 정상 채번된다. tb_llm_usage ·
tb_audit_log · tb_chunks 가 월별 파티션이라 이것을 먼저 확인했다.

전환 절차. 기본값을 떼고 → 옛 시퀀스를 지우고 → IDENTITY 를 붙이고 → 현재 최대값
다음으로 재시작한다. 마지막 단계를 빼먹으면 새 시퀀스가 1 부터 시작해 기존 행과
충돌한다. 빈 표는 1 로 둔다.

Revision ID: c9e1f2a3b4d5
Revises: f0a1b2c3d4e5

⚠ 초안은 a1b2c3d4e5f6 을 썼는데 그것이 이미 pg_rag_vectorstore 가 쓰는 번호였다.
로컬 DB 에 alembic upgrade head 를 돌려 "Cycle is detected" 로 잡았다.
Create Date: 2026-08-29
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "c9e1f2a3b4d5"
down_revision = "f0a1b2c3d4e5"
branch_labels = None
depends_on = None

# (표, 칼럼) — models.py 의 Identity(always=True) 9 곳과 1:1 이다.
COLUMNS: list[tuple[str, str]] = [
    ("tb_classification_levels", "level_id"),
    ("tb_evaluation_factors", "factor_id"),
    ("tb_level_keywords", "keyword_id"),
    ("tb_classification_evidence", "evidence_id"),
    ("tb_corrections", "correction_id"),
    ("tb_llm_usage", "usage_id"),
    ("tb_audit_log", "audit_id"),
    ("tb_training_datasets", "id"),
    ("tb_guides", "id"),
]


def _identity_kind(conn, table: str, column: str) -> str | None:
    """'a'(always) · 'd'(by default) · None(아직 identity 아님)."""
    return conn.execute(
        sa.text(
            "select attidentity from pg_attribute "
            "where attrelid = to_regclass(:t) and attname = :c and attnum > 0"
        ),
        {"t": table, "c": column},
    ).scalar()


def _exists(conn, table: str) -> bool:
    return conn.execute(sa.text("select to_regclass(:t)"), {"t": table}).scalar() is not None


def upgrade() -> None:
    conn = op.get_bind()
    for table, column in COLUMNS:
        if not _exists(conn, table):
            continue
        if _identity_kind(conn, table, column):
            continue  # 이미 IDENTITY 다(재실행 안전)

        # ① SERIAL 이 달아 둔 nextval 기본값과 그 시퀀스를 뗀다.
        seq = conn.execute(
            sa.text("select pg_get_serial_sequence(:t, :c)"), {"t": table, "c": column}
        ).scalar()
        op.execute(f'ALTER TABLE {table} ALTER COLUMN {column} DROP DEFAULT')
        if seq:
            op.execute(f"DROP SEQUENCE IF EXISTS {seq}")

        # ② 표준 IDENTITY 를 붙인다.
        op.execute(
            f"ALTER TABLE {table} ALTER COLUMN {column} ADD GENERATED ALWAYS AS IDENTITY"
        )

        # ③ 기존 최대값 다음으로 재시작. 이 단계를 빼면 1 부터 시작해 충돌한다.
        nxt = conn.execute(sa.text(f"select coalesce(max({column}), 0) + 1 from {table}")).scalar()
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} RESTART WITH {nxt}")


def downgrade() -> None:
    """IDENTITY 를 떼고 SERIAL 과 같은 형태(전용 시퀀스 + nextval 기본값)로 되돌린다."""
    conn = op.get_bind()
    for table, column in COLUMNS:
        if not _exists(conn, table):
            continue
        if not _identity_kind(conn, table, column):
            continue

        nxt = conn.execute(sa.text(f"select coalesce(max({column}), 0) + 1 from {table}")).scalar()
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} DROP IDENTITY IF EXISTS")
        seq = f"{table}_{column}_seq"
        op.execute(f"CREATE SEQUENCE IF NOT EXISTS {seq} START WITH {nxt} OWNED BY {table}.{column}")
        op.execute(f"ALTER TABLE {table} ALTER COLUMN {column} SET DEFAULT nextval('{seq}')")
