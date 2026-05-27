"""N4: add missing indexes for hot query paths.

Revision ID: a1f2c3d4e5f6
Revises: 80d75521b95a
Create Date: 2026-05-28

신규 인덱스 6종 (init.sql v2 baseline 위에 추가):
  1. idx_audit_tenant_occurred  — audit_log(tenant_id, occurred_at)
                                  — 테넌트별 감사 로그 시계열 조회
  2. idx_cls_tenant_classified  — classifications(tenant_id, classified_at)
                                  — 테넌트 스코프 최근 분류 시계열
  3. idx_doc_tenant_uploaded    — documents(tenant_id, uploaded_at)
                                  — 테넌트별 최신 문서 조회
  4. idx_tr_created             — training_runs(created_at)
                                  — list_recent_runs() ORDER BY created_at DESC
  5. idx_sd_status_created      — sample_documents(review_status, created_at)
                                  — list_pending_review() WHERE+ORDER BY
  6. idx_audit_tenant_action    — audit_log(tenant_id, action, occurred_at)
                                  — 테넌트 + 액션 복합 감사 추적

설계 노트:
- 모든 인덱스는 IF NOT EXISTS 로 idempotent — init.sql 재bootstrap 시에도 안전.
- audit_log/classifications 일부 인덱스는 RANGE PARTITION 부모에 생성:
  자식 파티션마다 자동 전파 (PG 11+ 표준 동작).
- CREATE INDEX CONCURRENTLY 는 트랜잭션 외부에서만 가능 → alembic의 트랜잭션
  컨텍스트에서는 일반 CREATE INDEX 사용. 운영 환경 대용량 테이블에는
  별도 maintenance window 에서 CONCURRENTLY 로 재생성 검토.
"""
from typing import Sequence, Union

from alembic import op


revision: str = "a1f2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "80d75521b95a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_NEW_INDEXES: list[tuple[str, str]] = [
    (
        "idx_audit_tenant_occurred",
        "CREATE INDEX IF NOT EXISTS idx_audit_tenant_occurred "
        "ON audit_log (tenant_id, occurred_at)",
    ),
    (
        "idx_cls_tenant_classified",
        "CREATE INDEX IF NOT EXISTS idx_cls_tenant_classified "
        "ON classifications (tenant_id, classified_at)",
    ),
    (
        "idx_doc_tenant_uploaded",
        "CREATE INDEX IF NOT EXISTS idx_doc_tenant_uploaded "
        "ON documents (tenant_id, uploaded_at)",
    ),
    (
        "idx_tr_created",
        "CREATE INDEX IF NOT EXISTS idx_tr_created "
        "ON training_runs (created_at)",
    ),
    (
        "idx_sd_status_created",
        "CREATE INDEX IF NOT EXISTS idx_sd_status_created "
        "ON sample_documents (review_status, created_at)",
    ),
    (
        "idx_audit_tenant_action",
        "CREATE INDEX IF NOT EXISTS idx_audit_tenant_action "
        "ON audit_log (tenant_id, action, occurred_at)",
    ),
]


def upgrade() -> None:
    """Upgrade schema — create 6 hot-path indexes (idempotent)."""
    for _name, ddl in _NEW_INDEXES:
        op.execute(ddl)


def downgrade() -> None:
    """Downgrade schema — drop the 6 hot-path indexes."""
    for name, _ddl in _NEW_INDEXES:
        op.execute(f"DROP INDEX IF EXISTS {name}")
