"""승인본 ↔ 학습셋 판 연결 — append-only. 칼럼 방식(어제 판)을 되돌린다.

왜 바꾸나(2026-09-05 지적). tb_sample_documents.added_to_dataset_version 은 UPDATE 로
덮어써서 **한 문서가 여러 판에 들어간 이력을 잃는다.** 재방출 한 번이면 앞선 판 기록이
사라진다. 이력을 남기려면 칼럼이 아니라 연결 표여야 한다.

같은 표에서 생성 작업 연결도 푼다 — synth_job_id 가 어디에도 없어 "이 작업이 만든 문서"를
물을 수 없었다(응답은 synth_job_id 를 주는데 그 뒤로 이어지는 곳이 없었다).

UNIQUE(sample_id, dataset_version) — 같은 판에 두 번 넣지 않는다. 재방출은 같은 내용이면
같은 판 이름이라 무해하게 부딪히고, 내용이 바뀌면 새 판으로 한 줄 더 쌓인다.

Revision ID: b0c1d2e3f4a5
Revises: a9b0c1d2e3f4
Create Date: 2026-09-05
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b0c1d2e3f4a5"
down_revision = "a9b0c1d2e3f4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 어제 판의 칼럼을 걷는다 — 실 데이터가 없고, 남겨 두면 표와 어느 쪽이 진실인지 갈린다.
    op.drop_index("idx_sd_dataset_version", table_name="tb_sample_documents")
    op.drop_column("tb_sample_documents", "added_to_dataset_version")

    op.create_table(
        "tb_sample_dataset_membership",
        sa.Column("membership_id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "sample_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("tb_sample_documents.sample_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("dataset_version", sa.String(64), nullable=False),
        sa.Column("synth_job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "sample_id", "dataset_version", name="uq_sdm_sample_version",
        ),
    )
    op.create_index(
        "idx_sdm_version", "tb_sample_dataset_membership", ["dataset_version"],
    )
    op.create_index(
        "idx_sdm_job", "tb_sample_dataset_membership", ["synth_job_id"],
    )


def downgrade() -> None:
    op.drop_index("idx_sdm_job", table_name="tb_sample_dataset_membership")
    op.drop_index("idx_sdm_version", table_name="tb_sample_dataset_membership")
    op.drop_table("tb_sample_dataset_membership")
    op.add_column(
        "tb_sample_documents",
        sa.Column("added_to_dataset_version", sa.String(64), nullable=True),
    )
    op.create_index(
        "idx_sd_dataset_version", "tb_sample_documents", ["added_to_dataset_version"],
    )
