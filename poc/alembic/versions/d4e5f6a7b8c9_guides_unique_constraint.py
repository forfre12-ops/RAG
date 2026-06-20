"""guides 멱등성 — (guide_id, version, tenant_id) UNIQUE 제약 추가.

Revision ID: d4e5f6a7b8c9
Revises: c7d8e9f0a1b2
Create Date: 2026-06-20

GuideRepo.upsert는 select-then-insert라 락이 없어 동시 업로드 시 같은
(guide_id, version, tenant_id) 중복 행을 만들 수 있다(버전 이력 중복 →
latest_training_version 비결정). DB UNIQUE를 최종 방어선으로 추가한다.

기존 중복 행이 있으면 제약 생성이 실패하므로 max(id)만 남기고 선정리한다.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d4e5f6a7b8c9"
down_revision: Union[str, Sequence[str], None] = "c7d8e9f0a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT = "uq_guides_id_version_tenant"


def upgrade() -> None:
    # 1) 기존 중복 정리 — 같은 (guide_id, version, tenant_id) 중 가장 큰 id만 유지.
    op.execute(
        """
        DELETE FROM tb_guides a
        USING tb_guides b
        WHERE a.id < b.id
          AND a.guide_id = b.guide_id
          AND a.version  = b.version
          AND a.tenant_id = b.tenant_id;
        """
    )
    # 2) UNIQUE 제약 생성.
    op.create_unique_constraint(
        _CONSTRAINT, "tb_guides", ["guide_id", "version", "tenant_id"]
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "tb_guides", type_="unique")
