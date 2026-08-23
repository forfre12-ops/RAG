"""Alembic 실행 환경. koipa.db.Base.metadata와 settings.database_url을 사용.

2026-05-28: init.sql 외부 부트스트랩 폐기. 모든 DDL은 alembic 단일 경로로 일원화.
- 신규 DB: `alembic upgrade head` 한 번으로 baseline + 모든 후속 revision 적용.
- 기존 production DB(80d75521b95a stamp 완료 상태): 자동 호환, 후속 revision만 적용.
"""

from logging.config import fileConfig

from sqlalchemy import engine_from_config, pool

import re

from alembic import context

# koipa DB 모듈 import — Base.metadata에 모든 테이블 등록.
from koipa.config import settings
from koipa.db import Base
from koipa.db import models as _models  # 부수효과: 19 ORM 클래스 → Base.metadata 등록

assert _models  # 미사용 import 경고 차단 + 등록 실행 보장


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# alembic.ini의 sqlalchemy.url은 비워두고 settings에서 동적 주입.
config.set_main_option("sqlalchemy.url", settings.database_url)

target_metadata = Base.metadata


# [2026-08-11] 런타임 생성 월 파티션을 autogenerate 비교에서 제외한다.
# services/partitions.ensure_partitions 가 beat 주기로 `tb_chunks_2026_11` 같은 자식 테이블을
# 만든다. ORM 에는 부모만 선언돼 있으므로(정상) autogenerate 는 자식들을 "모델에 없는 테이블"로
# 보고 전부 drop 하려 든다. 그래서 파티션이 생긴 환경에서는 DB-ORM 드리프트 게이트가 항상
# 실패했다 — 실측(2026-08-11): tb_chunks_2026_11 ~ 2027_06 에 대한 remove_table/remove_index.
# CI 는 파티션이 없는 새 DB 라 통과해서 여태 드러나지 않았다. 즉 게이트가 **실환경에서는
# 쓸 수 없는 상태**였다.
# 부모 테이블(PARTITIONED_TABLES)은 그대로 비교 대상이라 실제 스키마 드리프트는 계속 잡힌다.
_PARTITION_PARENTS = ("tb_chunks", "tb_llm_usage", "tb_audit_log")
# 실측(2026-08-11) 자식 테이블 접미사는 월(YYYY_MM)과 catch-all(default) 두 가지뿐이다.
_PARTITION_SUFFIX = re.compile(r"_(?:\d{4}_\d{2}|default)$")


# 마이그레이션이 만들지만 ORM 에는 선언하지 않는 테이블. pgvector 경로(adapters/vectorstore/
# pg_store.py)가 raw SQL 로 다루므로 모델 클래스가 없는 것이 정상이다. 선언이 없으니
# autogenerate 는 "모델에 없는 테이블"로 보고 drop 하려 든다.
# 실측(2026-08-11): DB 실테이블 67개 중 ORM 미선언은 파티션을 빼면 **이 둘뿐**이다.
_MIGRATION_ONLY_TABLES = frozenset({"tb_rag_aliases", "tb_rag_vectors"})

# DB 가 계산하는 생성 컬럼(GENERATED ALWAYS AS ... STORED). ORM 은 의도적으로 선언하지 않는다
# — 쓰기 대상이 아니기 때문이고, models.py 에도 그렇게 적혀 있다. autogenerate 는 그 의도를
# 알 수 없어 "모델에 없는 컬럼"으로 보고 drop 하려 든다.
# 실측(2026-08-11): tb_llm_usage.total_tokens = GENERATED ALWAYS AS (input_tokens + output_tokens).
_GENERATED_COLUMNS = frozenset({("tb_llm_usage", "total_tokens")})


def _is_runtime_partition(name: str) -> bool:
    return any(
        name.startswith(parent + "_") and _PARTITION_SUFFIX.search(name)
        for parent in _PARTITION_PARENTS
    )


def _is_out_of_orm(name: str) -> bool:
    return name in _MIGRATION_ONLY_TABLES or _is_runtime_partition(name)


def include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001,ARG001
    """반영된(reflected) 런타임 파티션과 그 인덱스만 제외 — 모델 쪽은 건드리지 않는다."""
    if not reflected or not name:
        return True
    if type_ == "table":
        return not _is_out_of_orm(name)
    if type_ == "index":
        parent = getattr(getattr(obj, "table", None), "name", "") or ""
        return not _is_out_of_orm(parent)
    if type_ == "column":
        parent = getattr(getattr(obj, "table", None), "name", "") or ""
        return (parent, name) not in _GENERATED_COLUMNS
    return True


def run_migrations_offline() -> None:
    """Offline 모드 — SQL 파일 생성용. DBAPI 불필요."""
    context.configure(
        url=settings.database_url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_object=include_object,
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
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
