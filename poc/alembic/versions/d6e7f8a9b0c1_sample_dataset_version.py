"""승인 합성본이 어느 학습셋 판에 들어갔는지 남긴다.

왜(2026-09-05 지적). SynthReviewResponse 에 added_to_dataset_version 필드가 있는데 값이
늘 None 이었다 — **표에 칸이 없었다.** 승인 → 학습 연결이 수동 스크립트뿐이고, 돌린 뒤에도
"이 승인본이 어느 셋에 들어갔나"를 되짚을 수 없었다. 검수는 했는데 그 결과가 어디로 갔는지
모르는 상태다.

자동 편입을 만들지 않는다 — 승인분이 바로 학습으로 흘러가면 검수가 형식이 된다.
**추적만 남긴다.** 빌드 스크립트가 방출한 셋에 판 이름을 찍고 그 값을 행에 되쓴다.

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-09-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "d6e7f8a9b0c1"
down_revision = "c5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "tb_sample_documents",
        sa.Column("added_to_dataset_version", sa.String(64), nullable=True),
    )
    op.create_index(
        "idx_sd_dataset_version",
        "tb_sample_documents",
        ["added_to_dataset_version"],
    )


def downgrade() -> None:
    op.drop_index("idx_sd_dataset_version", table_name="tb_sample_documents")
    op.drop_column("tb_sample_documents", "added_to_dataset_version")
