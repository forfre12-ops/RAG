"""tb_sample_documents 에 label_source·parse_error 컬럼 추가 — 합성 검수큐 마커 보존.

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-07-01

P0#1: synthesize_batch 워커가 생성 결과를 검수큐(tb_sample_documents)에 적재하도록 마감하면서,
생성기(SynthDoc)의 본문 출처 마커를 보존할 컬럼을 추가한다.
- label_source: None=정상 JSON 생성 / "noop_fallback"=placeholder(학습 편입 금지) / "llm_nonjson"=실 LLM 비-JSON.
- parse_error: 파싱 실패 사유(비-JSON 응답 등) — 학습 데이터 위생 필터의 근거.

둘 다 nullable(기존 행은 NULL=정상). 멱등: ADD COLUMN IF NOT EXISTS.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tb_sample_documents "
        "ADD COLUMN IF NOT EXISTS label_source VARCHAR(30)"
    )
    op.execute(
        "ALTER TABLE tb_sample_documents "
        "ADD COLUMN IF NOT EXISTS parse_error TEXT"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE tb_sample_documents DROP COLUMN IF EXISTS parse_error")
    op.execute("ALTER TABLE tb_sample_documents DROP COLUMN IF EXISTS label_source")
