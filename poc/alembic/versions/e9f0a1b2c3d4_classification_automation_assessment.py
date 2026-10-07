"""자동확정 정책 그림자 모드의 판단 근거를 classification에 보존.

Revision ID: e9f0a1b2c3d4
Revises: d8e9f0a1b2c3
Create Date: 2026-08-23

기존 행은 모델 점수 분포·게이트 사유를 완전하게 복원할 수 없다. 임의 백필로 학습
데이터를 오염시키지 않고 NULL로 유지하며, 이 마이그레이션 이후 생성된 분류만 분석한다.
"""

from typing import Sequence, Union

from alembic import op


revision: str = "e9f0a1b2c3d4"
down_revision: Union[str, Sequence[str], None] = "d8e9f0a1b2c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tb_classifications "
        "ADD COLUMN IF NOT EXISTS automation_assessment JSONB"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE tb_classifications "
        "DROP COLUMN IF EXISTS automation_assessment"
    )
