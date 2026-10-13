"""전역 직렬화 잠금 전용 표 — 두 dialect 에서 같은 수명을 갖는 잠금.

왜(2026-09-05). 감사 체인(prev 읽기+INSERT)과 모델 활성 전환이 `pg_advisory_xact_lock`
으로만 잠겨 있었다. 호출부는 dialect 가 PostgreSQL 일 때만 실행하거나 예외를 흡수해서,
MariaDB 로 옮기면 **두 임계영역이 조용히 열린다**. 감사 체인이 분기하거나 두 재학습이
활성을 다투는데 아무 신호도 나지 않는 상태다.

MariaDB 의 GET_LOCK 으로 메우려다 실측에서 두 번 깨졌다(자세한 것은 db/locks.py 머리말) —
커넥션 단위라 트랜잭션 단위 수명을 만들 수 없었다. `SELECT ... FOR UPDATE` 행 잠금은
PostgreSQL·MariaDB 양쪽에서 트랜잭션 단위이고 commit/rollback 에 자동으로 풀린다.
호출부가 이미 전제하던 바로 그 수명이라 호출 모양을 바꾸지 않아도 된다.

행은 데이터를 담지 않는다 — 행의 존재 자체가 잠금 지점이고, 처음 쓸 때 자동 생성된다.

MariaDB 계열(f2a3b4c5d6e7)은 Base.metadata.create_all 이라 models.py 정의에서 자동으로
나온다 — 이 판은 PostgreSQL 계열 전용이다.

Revision ID: c5d6e7f8a9b0
Revises: b4c5d6e7f8a9
Create Date: 2026-09-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "c5d6e7f8a9b0"
down_revision = "b4c5d6e7f8a9"
branch_labels = None
depends_on = None


# 잠금 지점 — 행이 **미리 있어야** 한다(아래 실측 참조).
LOCK_NAMES = ["audit_chain", "model_activation"]


def upgrade() -> None:
    op.create_table(
        "tb_advisory_locks",
        sa.Column("name", sa.String(64), primary_key=True),
        if_not_exists=True,
    )
    # ⚠ 행을 런타임에 만들면 첫 사용에서 잠금이 통째로 열린다(2026-09-05 실측).
    #   동시 요청이 각자 INSERT 를 시도하면 한쪽만 성공하는데, 그 행이 아직 커밋되지
    #   않아 **다른 쪽의 SELECT ... FOR UPDATE 가 행을 못 찾는다.** 못 찾으면 잠금
    #   없이 진행하므로 24건 동시 삽입에서 감사 체인이 실제로 분기했다
    #   (MariaDB 5지점 · PostgreSQL 2지점). 그래서 여기서 심는다.
    for name in LOCK_NAMES:
        op.execute(
            sa.text("INSERT INTO tb_advisory_locks (name) VALUES (:n)").bindparams(n=name)
        )


def downgrade() -> None:
    op.drop_table("tb_advisory_locks", if_exists=True)
