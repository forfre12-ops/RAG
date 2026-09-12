"""tb_sample_documents 에 corrected_level_id 추가 — 검수자가 고친 등급을 학습행에 반영.

Revision ID: f0a1b2c3d4e5
Revises: e9f0a1b2c3d4
Create Date: 2026-08-24

왜. POST /synth/{id}/review 는 진작부터 corrected_grade 를 **받고** 있었고 제출본(부록B
API명세)도 "검수 결정(decision, corrected_grade?, comment?, actor)을 적용하고" 로 서술하는데,
서버가 그 값을 어디에도 쓰지 않았다. SynthesisService.review() 가 repo 에 넘기는 인자는
approved/reviewed_by/rejection_reason 뿐이고, build_training_rows 의 라벨은 target_level_id
에서 나온다. 검수자가 등급을 고쳐 승인해도 **원래 목표 등급으로 학습행이 만들어졌다** —
사람의 교정이 조용히 버려지는 경로였다.

target_level_id 를 덮어쓰지 않고 별도 컬럼을 둔다. 덮어쓰면 "생성 때 어느 등급을 요구했나"
와 "검수자가 무엇으로 고쳤나"가 한 칸에 뭉개져, 교정이 있었다는 사실 자체가 사라진다.
감사에서 되짚을 것은 결과가 아니라 그 차이다.

nullable(기존 행은 NULL = 교정 없음, target_level_id 그대로). 멱등: ADD COLUMN IF NOT EXISTS.
FK 는 target_level_id 와 같은 tb_classification_levels(level_id) · ON DELETE RESTRICT.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "f0a1b2c3d4e5"
down_revision: Union[str, Sequence[str], None] = "e9f0a1b2c3d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tb_sample_documents "
        "ADD COLUMN IF NOT EXISTS corrected_level_id INTEGER"
    )
    # FK 는 재실행 가능하도록 존재 확인 후 추가(ADD CONSTRAINT 에는 IF NOT EXISTS 가 없다).
    op.execute(
        """
        DO $$
        BEGIN
            IF NOT EXISTS (
                SELECT 1 FROM pg_constraint
                WHERE conname = 'fk_sd_corrected_level'
            ) THEN
                ALTER TABLE tb_sample_documents
                ADD CONSTRAINT fk_sd_corrected_level
                FOREIGN KEY (corrected_level_id)
                REFERENCES tb_classification_levels(level_id) ON DELETE RESTRICT;
            END IF;
        END $$;
        """
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE tb_sample_documents "
        "DROP CONSTRAINT IF EXISTS fk_sd_corrected_level"
    )
    op.execute(
        "ALTER TABLE tb_sample_documents DROP COLUMN IF EXISTS corrected_level_id"
    )
