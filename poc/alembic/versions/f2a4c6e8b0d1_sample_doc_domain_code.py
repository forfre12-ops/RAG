"""합성문서의 생성 주제와 실제 문서 유형을 분리한다.

Revision ID: f2a4c6e8b0d1
Revises: e3a7c9f1b5d2
Create Date: 2026-10-11

``tad_sm_syn_doc_mng.doc_knd_nm``에는 원래 실제 문서 유형(예: 시험성적서,
사업전략서)을 남겨야 한다. 그러나 워커가 생성 주제(예: finance, 반도체)를 이 칼럼에
넣고 있어, 생성 분포와 문서 유형을 분리해 감사할 수 없었다.

새 ``dmn_cd``는 앞으로 생성 주제를 보존한다. 기존 행은 어느 값이 원래 문서 유형이고
어느 값이 잘못 저장된 주제인지 확정할 근거가 없으므로 억지로 backfill하지 않는다. ORM과
응답은 기존 행에서만 doc_knd_nm을 주제로 해석하는 호환 fallback을 둔다.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "f2a4c6e8b0d1"
down_revision: Union[str, Sequence[str], None] = "e3a7c9f1b5d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE tad_sm_syn_doc_mng "
        "ADD COLUMN IF NOT EXISTS dmn_cd VARCHAR(50)"
    )


def downgrade() -> None:
    op.execute(
        "ALTER TABLE tad_sm_syn_doc_mng "
        "DROP COLUMN IF EXISTS dmn_cd"
    )
