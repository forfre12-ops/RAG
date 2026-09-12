"""활성키 칼럼을 표준 형식단어에 맞춘다 — actvtn_key → actvtn_sn

왜(2026-09-12). `7b3e9d2a4f10` 이 표·칼럼을 KOIPA 표준용어집 이름으로 바꿀 때 이 칼럼만
규칙을 벗어났다. 용어집에서 `키(KEY)` 는 등재 단어이지만 **형식단어여부 = N** 이라 용어의
끝자리로 쓸 수 없다. 전수로 확인했다(표준용어 13,704개):

    영문약어가 KEY 로 끝나는 표준용어  0 / 13,704
    '키' 로 끝나는 표준도메인          없음
    흔한 끝자리                       NM 2,264 · YMD 1,828 · YN 1,472 · CN 928 · SN 259

칼럼 값은 **활성일 때 1, 아니면 NULL** 인 SMALLINT 이고, 유니크 인덱스로 "활성 모델은
최대 하나"를 강제한다. 형식단어 SN(일련번호, 형식단어여부=Y)이 값의 성격과도 맞는다.

⚠ `7b3e9d2a4f10` 을 고치지 않는다. 그 판은 이미 서버에서 돌았다 — 고치면 이미 올린 DB 와
  새로 만드는 DB 의 이름이 갈린다. 그래서 한 번 더 바꾸고, 무엇을 왜 바꿨는지는
  `db/standard_names.POST_BASE_RENAMES` 에 남긴다.

PostgreSQL 에서 `RENAME COLUMN` 은 그 칼럼을 쓰는 인덱스 정의를 함께 따라온다
(인덱스는 칼럼을 이름이 아니라 번호로 잡는다). 생성식 `CASE WHEN actvtn_yn THEN 1 END` 은
다른 칼럼을 참조하므로 영향이 없다.
"""

from alembic import op
import sqlalchemy as sa

revision = "9c4e1f7a2b58"
down_revision = "7b3e9d2a4f10"
branch_labels = None
depends_on = None

_TABLE = "tad_mm_mdl_ver_mng"


def _has_column(bind, table: str, column: str) -> bool:
    return column in {c["name"] for c in sa.inspect(bind).get_columns(table)}


def upgrade() -> None:
    bind = op.get_bind()
    # 이미 바뀐 DB 에서 다시 돌아도 죽지 않게 한다(재적용 안전).
    if _has_column(bind, _TABLE, "actvtn_key"):
        op.execute(f"ALTER TABLE {_TABLE} RENAME COLUMN actvtn_key TO actvtn_sn")


def downgrade() -> None:
    bind = op.get_bind()
    if _has_column(bind, _TABLE, "actvtn_sn"):
        op.execute(f"ALTER TABLE {_TABLE} RENAME COLUMN actvtn_sn TO actvtn_key")
