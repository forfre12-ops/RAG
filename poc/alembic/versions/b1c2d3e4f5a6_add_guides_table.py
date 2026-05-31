"""N5: guides 테이블 추가 — GuideService in-memory → DB.

Revision ID: b1c2d3e4f5a6
Revises: a1f2c3d4e5f6
Create Date: 2026-05-31

가이드 문서 메타(버전 이력)를 in-memory dict 에서 PG 테이블로 이전.
서버 재시작 시 메타 소멸 방지, 다중 워커 일관성 보장.
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa

revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, None] = "a1f2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "guides",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column("guide_id", sa.String(200), nullable=False),
        sa.Column("tenant_id", sa.String(50), nullable=False, server_default="default"),
        sa.Column("version", sa.String(50), nullable=False),
        sa.Column("effective_date", sa.String(30), nullable=True),
        sa.Column("change_summary", sa.Text, nullable=True),
        sa.Column("doc_type", sa.String(50), nullable=True),
        sa.Column("filename", sa.String(500), nullable=True),
        sa.Column("indexed", sa.Boolean, nullable=False, server_default="false"),
        sa.Column("embedding_vector_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("index_name", sa.String(300), nullable=True),
        sa.Column("alias", sa.String(300), nullable=True),
        sa.Column("model", sa.String(100), nullable=True),
        sa.Column("registered_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("now()")),
    )
    op.create_index("idx_guides_guide_id", "guides", ["guide_id"])
    op.create_index("idx_guides_tenant_guide", "guides", ["tenant_id", "guide_id"])
    op.create_index("idx_guides_registered", "guides", ["registered_at"])


def downgrade() -> None:
    op.drop_index("idx_guides_registered", table_name="guides")
    op.drop_index("idx_guides_tenant_guide", table_name="guides")
    op.drop_index("idx_guides_guide_id", table_name="guides")
    op.drop_table("guides")
