"""파티션 롤오버 백필 (#5) — 향후 13개월 월 파티션 선생성.

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-06-20

baseline_init은 정적 월 파티션만 만든다(audit_log는 2026-06, chunks/llm_usage는
2026-07까지). 그 이후 행은 `*_default`로 쌓여 프루닝이 무력화되고, 나중에 정상 파티션
attach 시 default 검증 락이 운영을 막는다. 본 마이그레이션이 2026-06~2027-06(13개월)
파티션을 선생성하고, 이후는 beat `ensure_partitions_tick`이 매일 연장한다.

결정론적(literal 범위) — `CREATE TABLE IF NOT EXISTS`라 기존 파티션은 no-op.
배포 시점(default 비어 있음)엔 충돌 없음. downgrade는 데이터 손실 방지 위해 no-op.
"""
from __future__ import annotations

import datetime as dt
from typing import Sequence, Union

from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_TABLES = ("tb_chunks", "tb_llm_usage", "tb_audit_log")


def _months(start_year: int, start_month: int, count: int):
    y, m = start_year, start_month
    for _ in range(count):
        start = dt.date(y, m, 1)
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        end = dt.date(ny, nm, 1)
        yield start, end
        y, m = ny, nm


def upgrade() -> None:
    for table in _TABLES:
        for start, end in _months(2026, 6, 13):
            part = f"{table}_{start:%Y_%m}"
            op.execute(
                f"CREATE TABLE IF NOT EXISTS {part} PARTITION OF {table} "
                f"FOR VALUES FROM ('{start.isoformat()}') TO ('{end.isoformat()}')"
            )


def downgrade() -> None:
    # 데이터 손실 방지 — 파티션 자동 삭제 안 함(필요 시 수동 DROP).
    pass
