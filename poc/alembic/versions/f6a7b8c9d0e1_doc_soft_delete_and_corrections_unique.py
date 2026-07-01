"""문서 soft-delete(#38) + corrections UNIQUE(#26b) 최종 방어선.

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-06-20

#38 (보존정책): tb_documents에 deleted_at(nullable, 기본 NULL) 컬럼을 추가한다.
NULL=활성 행, NOT NULL=논리 삭제. 물리 삭제/cascade는 그대로 두고(DocumentService
기본 경로), soft_delete()가 deleted_at=NOW()를 세팅하면 조회에서만 숨긴다.
기본값이 NULL이라 기존 행/테스트 비파괴. 보존기간 만료 후 물리 purge 잡은 범위 밖.

#26b (최종 방어선, Low): tb_corrections에 (classification_id, corrected_level_id,
corrected_by) UNIQUE 제약을 추가한다. confirm/보정 경로가 select-then-insert일 때
race로 같은 조합 중복이 생기면 active learning 라벨이 중복 가중되므로 DB로 막는다.
기존 중복 행이 있으면 제약 생성이 실패하므로 max(correction_id)만 남기고 선정리한다
(guide UNIQUE 마이그레이션 d4e5f6a7b8c9의 dedup+create 패턴 동일).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CORR_CONSTRAINT = "uq_corr_cls_level_by"


def upgrade() -> None:
    # (a) #38 — tb_documents.deleted_at 컬럼 추가(nullable, 기본 NULL).
    op.add_column(
        "tb_documents",
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
    )

    # (b) #26b — 기존 중복 정리. 같은 (classification_id, corrected_level_id,
    # corrected_by) 중 가장 큰 correction_id만 유지.
    op.execute(
        """
        DELETE FROM tb_corrections a
        USING tb_corrections b
        WHERE a.correction_id < b.correction_id
          AND a.classification_id = b.classification_id
          AND a.corrected_level_id = b.corrected_level_id
          AND a.corrected_by = b.corrected_by;
        """
    )

    # (c) #26b — UNIQUE 제약 생성.
    op.create_unique_constraint(
        _CORR_CONSTRAINT,
        "tb_corrections",
        ["classification_id", "corrected_level_id", "corrected_by"],
    )


def downgrade() -> None:
    op.drop_constraint(_CORR_CONSTRAINT, "tb_corrections", type_="unique")
    op.drop_column("tb_documents", "deleted_at")
