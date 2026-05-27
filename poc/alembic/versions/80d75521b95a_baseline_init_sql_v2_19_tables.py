"""baseline (historical): now a NO-OP — 실제 스키마는 000000000001로 이관.

Revision ID: 80d75521b95a
Revises: 000000000001
Create Date: 2026-05-27 21:03:03.028623

배경:
- 본 revision은 원래 'psql -f migrations/init.sql' 외부 부트스트랩의 stamp 목적이었음.
- 2026-05-28: init.sql ↔ alembic 이중 유지 해소를 위해 baseline을 `000000000001_baseline_init.py`로
  이관. 본 파일은 기존 production DB의 alembic_version 호환을 위해 NO-OP로 유지.

체인:
  000000000001 (실제 baseline DDL)
    └─ 80d75521b95a (본 파일 — NO-OP, 호환 유지)
        └─ a1f2c3d4e5f6 (N4 hot-path indexes)

기존 production DB는 별도 마이그레이션 없이 자동 호환됨:
- 이미 80d75521b95a가 stamp되어 있다면 → 다음 `alembic upgrade head`에서 a1f2c3d4e5f6만 적용.
- 신규 DB는 000000000001부터 정상 적용.
"""
from typing import Sequence, Union


revision: str = "80d75521b95a"
down_revision: Union[str, Sequence[str], None] = "000000000001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """No-op — 실제 스키마는 000000000001에서 생성."""


def downgrade() -> None:
    """No-op — baseline 후속 revision이므로 downgrade도 NO-OP."""
