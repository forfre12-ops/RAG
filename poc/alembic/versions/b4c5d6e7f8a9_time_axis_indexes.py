"""시간축 선두 인덱스 3개 — 파티션 프루닝이 받던 자리를 인덱스가 받는다.

왜(2026-09-05). MariaDB 전환에서 파티션을 쓰지 않기로 했다. 실측 근거는
services/partitions.py 머리말 — 223 실서버 30일치 감사로그가 71,161행·30MB(연 환산
약 87만행)로 파티션이 필요한 규모가 아니고, 파티션의 두 효용 중 보존기간 삭제는 애초에
구현돼 있지 않았다(오래된 파티션을 떼는 코드 0건).

문제는 세 표 모두 **시간 칼럼이 선두인 인덱스가 없다**는 것이었다:

    tb_audit_log   PK(audit_id, occurred_at) · idx_audit_actor(actor_id, occurred_at DESC)
                   · idx_audit_action(action, occurred_at DESC)
    tb_llm_usage   PK(usage_id, called_at) · idx_lu_phase(billing_phase, called_at DESC)
    tb_chunks      PK(chunk_id, created_at) · idx_chunk_doc(doc_id, chunk_index)

전부 시간 칼럼이 두 번째라, 날짜 범위만 주는 질의는 어느 것도 타지 못한다. PostgreSQL
에서는 파티션 프루닝이 그 자리를 받고 있었다. 실제 소비자:

    services/audit_chain.verify_chain   WHERE occurred_at BETWEEN .. ORDER BY occurred_at, audit_id
    services/retention.purge_expired    DELETE WHERE <시간칼럼> >= .. AND < ..   (신규)

PostgreSQL 에도 같이 넣는다 — 파티션이 있어도 파티션 안에서는 여전히 이 인덱스가 필요하고,
보존기간 삭제는 두 dialect 공통 경로다.

MariaDB 계열(f2a3b4c5d6e7)은 Base.metadata.create_all 이라 models.py 정의에서 자동으로
나온다 — 이 판은 PostgreSQL 계열 전용이다.

Revision ID: b4c5d6e7f8a9
Revises: a3b4c5d6e7f8
Create Date: 2026-09-05
"""
from __future__ import annotations

from alembic import op

revision = "b4c5d6e7f8a9"
down_revision = "a3b4c5d6e7f8"
branch_labels = None
depends_on = None

# (인덱스명, 표, 칼럼들) — models.py 의 __table_args__ 와 1:1 이다.
INDEXES: list[tuple[str, str, list[str]]] = [
    ("idx_audit_occurred", "tb_audit_log", ["occurred_at", "audit_id"]),
    ("idx_lu_called", "tb_llm_usage", ["called_at"]),
    ("idx_chunk_created", "tb_chunks", ["created_at"]),
]


def upgrade() -> None:
    for name, table, cols in INDEXES:
        op.create_index(name, table, cols, if_not_exists=True)


def downgrade() -> None:
    for name, table, _cols in INDEXES:
        op.drop_index(name, table_name=table, if_exists=True)
