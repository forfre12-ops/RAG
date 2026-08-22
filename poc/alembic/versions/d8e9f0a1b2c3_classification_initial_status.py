"""tb_classifications 에 initial_status 컬럼 추가 — 최초 게이트 판정 동결.

Revision ID: d8e9f0a1b2c3
Revises: a7b8c9d0e1f2
Create Date: 2026-08-22

배경: /dashboard/summary 의 auto_confirmed 는 현재 status='staging' 건수를 센다.
status 는 사람이 확인하면 confirmed 로 바뀌므로(confirm_service.confirm), 시간이
지날수록 "최초 자동확정" 이 아니라 "아직 확인 안 된 자동확정" 만 남아 분자가 준다.
initial_status 는 create_classification() 이 게이트 최종 결정(staging/needs_review/
needs_second_review)을 기록한 그 시점 값을 그대로 동결 보존하고, 이후 어떤 확정·교정
경로도 이 컬럼을 건드리지 않는다.

기존 행(nullable, 백필 없음): status='confirmed'/'corrected' 인 기존 행은 최초에
staging 이었는지 needs_review 였는지 이 테이블만으로는 구분 불가 — 잘못된 값으로
백필하는 대신 NULL로 남긴다. initial_status 기준 집계는 이 마이그레이션 이후 생성된
분류부터 정확하다.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "d8e9f0a1b2c3"
down_revision: Union[str, Sequence[str], None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tb_classifications "
        "ADD COLUMN IF NOT EXISTS initial_status VARCHAR(20)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_cls_initial_status "
        "ON tb_classifications(initial_status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_cls_initial_status")
    op.execute("ALTER TABLE tb_classifications DROP COLUMN IF EXISTS initial_status")
