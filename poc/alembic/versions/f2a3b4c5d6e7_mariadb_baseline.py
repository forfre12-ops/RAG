"""MariaDB 전용 베이스라인 — 독립된 alembic 계열(branch).

왜 별도 계열인가. 기존 계열(000000000001 ~ e1f2a3b4c5d6)은 18판 전부 PostgreSQL
raw SQL 이다(CREATE EXTENSION pg_trgm, JSONB, PARTITION BY RANGE, ... — 실측
2026-09: `alembic upgrade head` 를 MariaDB 에 그대로 걸면 1번 판 첫 문장에서
멈춘다). 18판을 한 판씩 MariaDB 문법으로 번역하는 것보다, 지금 확정된 스키마를
**한 판으로 새로 잡는 것**이 짧고 검증하기 쉽다 — models.py 가 이미 두 dialect
포터블(ef294c56)하므로 그 정의를 그대로 쓴다.

MariaDB 는 이 프로젝트에 새로 들어오는 대상이라(찍힌 DB 가 0대) 기존 계열과
이어 붙일 이유가 없다. `branch_labels`로 독립 계열을 만든다 — Postgres 계열
(223·로컬 koipa-testdb)은 이 판을 전혀 보지 않는다.

    DATABASE_URL=mariadb+pymysql://... alembic upgrade mariadb@head

DDL 은 `Base.metadata.create_all()` 로 낸다 — 19표 전체를 손으로 다시 옮겨
적으면 그 자체가 새로운 어긋남의 자리가 된다. 이미 models.py 가 정본이고
실제 MariaDB 10.11 컨테이너에 create_all 로 기동해 ORM 기능 시험(UUID·JSON·
배열·INET 왕복, 활성모델 유일성)까지 통과한 것을 실측 확인했다(ef294c56).

시드 데이터는 로컬 Postgres 시험 DB(koipa-testdb:15432, alembic head 적용
완료 상태)에서 실제로 조회해 그대로 옮겼다 — 등급 4행 그대로, 요건은 정본
3요건(SECRECY·VALUE·MANAGEMENT, is_active=True) 과 레거시 4요소(비활성,
FK 호환 목적)를 합쳐 7행. 검수 큐 표(tb_level_keywords 등)는 어느 계열에서도
마이그레이션이 시드하지 않는다 — 관리자 API 첫 쓰기 때 코드가 자동 승격한다
(services/keyword_admin_service.py 의 _ensure_seeded).

Revision ID: f2a3b4c5d6e7
Revises:
Branch Labels: mariadb
Create Date: 2026-09-04
"""
from __future__ import annotations

from alembic import op

revision = "f2a3b4c5d6e7"
down_revision = None
branch_labels = ("mariadb",)
depends_on = None

_LEVELS = [
    ("TS", "특급기밀", 1, "#7B1FA2", 3.0),
    ("S1", "1급 비밀", 2, "#D32F2F", 2.0),
    ("S2", "2급 대외비", 3, "#F57C00", 1.0),
    ("S3", "3급 공개", 4, "#388E3C", 1.0),
]

_FACTORS = [
    # (factor_code, factor_name, weight, is_active)
    ("ECONOMIC_VALUE", "경제적 가치", 0.30, False),
    ("NON_PUBLICITY", "비공지성", 0.25, False),
    ("MANAGEMENT_LEVEL", "관리수준", 0.15, False),
    ("LEAK_IMPACT", "유출 시 영향도", 0.30, False),
    ("SECRECY", "비공지성(S)", 1.0, True),
    ("VALUE", "경제적 유용성(V)", 1.0, True),
    ("MANAGEMENT", "비밀관리성(M)", 1.0, True),
]


def _require_mariadb(conn) -> None:
    name = conn.dialect.name
    if name != "mariadb":
        raise RuntimeError(
            f"이 판은 MariaDB 전용 독립 계열이다(dialect={name}). "
            "Postgres 에는 기존 계열(alembic upgrade head)을 쓸 것 — "
            "이 판은 `alembic upgrade mariadb@head` 로만 호출한다."
        )


def upgrade() -> None:
    conn = op.get_bind()
    _require_mariadb(conn)

    from koipa.db import Base  # noqa: PLC0415  (env.py 가 이미 models 를 등록해 둔다)

    Base.metadata.create_all(bind=conn)

    for code, name, order, color, loss_weight in _LEVELS:
        conn.exec_driver_sql(
            "INSERT INTO tb_classification_levels "
            "(level_code, level_name, level_order, color_hex, loss_weight) "
            "VALUES (%s, %s, %s, %s, %s)",
            (code, name, order, color, loss_weight),
        )

    for code, name, weight, is_active in _FACTORS:
        conn.exec_driver_sql(
            "INSERT INTO tb_evaluation_factors (factor_code, factor_name, weight, is_active) "
            "VALUES (%s, %s, %s, %s)",
            (code, name, weight, is_active),
        )


def downgrade() -> None:
    conn = op.get_bind()
    _require_mariadb(conn)

    from koipa.db import Base  # noqa: PLC0415

    Base.metadata.drop_all(bind=conn)
