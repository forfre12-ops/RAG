"""스키마 일관성 — 감리 DB 영역 지적(인쇄 71·79쪽)의 현행 코드 쪽 정리.

Revision ID: 5c1d9e0a7b34
Revises: f8a9b0c1d2e3
Create Date: 2026-09-10

scripts/audit_schema_consistency.py 가 현행 models.py 에서 센 불일치 중 **물리 형식**만 고친다.
명명(R1·R2)은 FK 칼럼을 역할별로 설명한 것이라 사람이 정할 일이고 여기서 건드리지 않는다.

    R3  tb_document_labels.confidence   NUMERIC(3,2) → NUMERIC(5,4)
        같은 이름의 tb_classifications.confidence 는 (5,4). 감리 도표 75 가 짚은 것과 같은 건.
        넓히는 방향이라 기존 값은 그대로 들어간다(0.00~9.99 ⊂ 0.0000~9.9999).
    R3  tb_training_runs.model_version  → model_version_id (이름만)
        tb_classifications.model_version 은 판정에 쓴 모델 **라벨 문자열**(VARCHAR)인데 여기는
        tb_model_versions 를 가리키는 **UUID FK** 였다. 같은 이름에 다른 뜻·형식.
        파이썬 속성명은 model_version 그대로라 호출부·API 응답은 바뀌지 않는다.
        인덱스 idx_tr_model 은 PostgreSQL 이 칼럼 이름 변경을 따라가므로 손대지 않는다.
    R4  created_at NOT NULL — 9개 표
        전부 server_default now() 인데 NULL 을 허용하고 있었다(tb_chunks 만 NOT NULL).
        ⚠ NULL 행이 있으면 이 판은 **멈춘다.** 생성 시각을 지어내 채우지 않는다 — 그 행이
        언제 생겼는지는 아무도 모른다. 멈추면 그 행을 사람이 보고 정한다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5c1d9e0a7b34"
down_revision: Union[str, None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

CREATED_AT_OWNERS = (
    "tb_classification_levels",
    "tb_evaluation_factors",
    "tb_level_keywords",
    "tb_classification_evidence",
    "tb_model_versions",
    "tb_training_runs",
    "tb_prompt_versions",
    "tb_sample_documents",
    "tb_sample_dataset_membership",
)


def upgrade() -> None:
    bind = op.get_bind()
    nulls = {}
    for t in CREATED_AT_OWNERS:
        n = bind.execute(sa.text(f"SELECT count(*) FROM {t} WHERE created_at IS NULL")).scalar()
        if n:
            nulls[t] = n
    if nulls:
        raise RuntimeError(
            "created_at 이 NULL 인 행이 있어 NOT NULL 로 바꾸지 않는다 — 생성 시각을 지어내지 "
            f"않는다. 행을 확인한 뒤 다시 올릴 것: {nulls}"
        )
    for t in CREATED_AT_OWNERS:
        op.alter_column(
            t, "created_at",
            existing_type=sa.DateTime(timezone=True),
            existing_server_default=sa.text("now()"),
            nullable=False,
        )
    op.alter_column(
        "tb_document_labels", "confidence",
        existing_type=sa.Numeric(3, 2), type_=sa.Numeric(5, 4), existing_nullable=True,
    )
    op.alter_column("tb_training_runs", "model_version", new_column_name="model_version_id")


def downgrade() -> None:
    op.alter_column("tb_training_runs", "model_version_id", new_column_name="model_version")
    # 좁히는 방향 — 소수 넷째 자리 값은 둘째 자리로 반올림된다.
    op.alter_column(
        "tb_document_labels", "confidence",
        existing_type=sa.Numeric(5, 4), type_=sa.Numeric(3, 2), existing_nullable=True,
        postgresql_using="round(confidence, 2)",
    )
    for t in CREATED_AT_OWNERS:
        op.alter_column(
            t, "created_at",
            existing_type=sa.DateTime(timezone=True),
            existing_server_default=sa.text("now()"),
            nullable=True,
        )
