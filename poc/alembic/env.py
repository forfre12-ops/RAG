"""Alembic 실행 환경. lloydk.db.Base.metadata와 settings.database_url을 사용.

2026-05-28: init.sql 외부 부트스트랩 폐기. 모든 DDL은 alembic 단일 경로로 일원화.
- 신규 DB: `alembic upgrade head` 한 번으로 baseline + 모든 후속 revision 적용.
- 기존 production DB(80d75521b95a stamp 완료 상태): 자동 호환, 후속 revision만 적용.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

from alembic import context

# lloydk DB 모듈 import — Base.metadata에 모든 테이블 등록.
from lloydk.config import settings
from lloydk.db import Base
from lloydk.db import models as _models  # 부수효과: 19 ORM 클래스 → Base.metadata 등록

assert _models  # 미사용 import 경고 차단 + 등록 실행 보장


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# alembic.ini의 sqlalchemy.url은 비워두고 settings에서 동적 주입.
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Offline 모드 — SQL 파일 생성용. DBAPI 불필요."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Online 모드 — 실제 DB 연결로 마이그레이션 실행."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
