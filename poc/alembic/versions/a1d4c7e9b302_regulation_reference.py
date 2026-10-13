"""규정 참고 표시 — 규정·조항·문장 표 3개.

회원사가 올린 사내 규정을 조항·문장으로 나눠 두고, 검수 화면이 문서와 관련된 규정 원문 문장을 참고로 보여 준다
(설계서 docs/CLAUDE_REGULATION_REFERENCE_DESIGN_20260925.md). ⛔ 등급 판정·검수 라우팅과 무관하다.

■ 왜 pgvector 표가 아닌가
    규정은 작다(조항 수십~수백, 문장 수백~수천). 벡터는 BYTEA(float32 little-endian)로 저장하고 프로세스
    메모리에서 정확 검색한다. `vector(1024)` 칼럼은 SQLAlchemy 코어가 못 다뤄 alembic autogenerate 가 DROP 을
    내므로(alembic/env.py `_MIGRATION_ONLY_TABLES` 사유) 이 표들을 일반 ORM 표로 두려면 BYTEA 가 낫다.

■ 이름
    표·칼럼 물리명은 표준 명명(db/standard_names.py POST_BASE_TABLES/COLUMNS)이다. **표준용어집과 대조 전인
    후보명**이라(용어집 파일이 저장소에 없다) 새로 지은 단어는 NEW_TERMS_FOR_GLOSSARY 에 모아 두었다 —
    자체표준 용어로 올려야 한다. 7b3e9d2a4f10 은 이미 서버에서 돈 판이라 고치지 않는다(그래서 POST_BASE_*).

■ 격리
    org 키가 없다 — 배포 하나 = 회원사 하나(테넌트 제거 결정).

■ 재적용 안전
    표가 이미 있으면 건너뛴다(개발 DB 에 수동으로 만든 표가 있어도 멈추지 않게).

Revision ID: a1d4c7e9b302
Revises: b7d3f5a19c24
Create Date: 2026-09-25
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a1d4c7e9b302"
down_revision = "b7d3f5a19c24"
branch_labels = None
depends_on = None

_REG = "tad_rm_rgltn_mng"
_CLAUSE = "tad_rm_rgltn_artcl_mng"
_SENT = "tad_rm_rgltn_stc_mng"


def _has_table(name: str) -> bool:
    return sa.inspect(op.get_bind()).has_table(name)


def upgrade() -> None:
    uuid_pk = dict(primary_key=True, server_default=sa.text("gen_random_uuid()"))

    if not _has_table(_REG):
        op.create_table(
            _REG,
            sa.Column("rgltn_id", sa.Uuid(as_uuid=True), **uuid_pk),
            sa.Column("rgltn_nm", sa.String(200), nullable=False),
            sa.Column("ver_lbl_nm", sa.String(50), nullable=False),
            sa.Column("enfc_dt", sa.String(30), nullable=True),
            sa.Column("prcs_stts_cd", sa.String(20), nullable=False, server_default=sa.text("'indexing'")),
            sa.Column("file_hash_nm", sa.String(64), nullable=False),
            sa.Column("orgtxt_path_nm", sa.String(500), nullable=True),
            sa.Column("orgnl_frmat_nm", sa.String(10), nullable=False),
            sa.Column("file_nm", sa.String(500), nullable=False),
            sa.Column("prttn_mth_cd", sa.String(30), nullable=True),
            sa.Column("artcl_cnt", sa.Integer, nullable=False, server_default=sa.text("0")),
            sa.Column("stc_cnt", sa.Integer, nullable=False, server_default=sa.text("0")),
            sa.Column("embd_mdl_nm", sa.String(200), nullable=True),
            sa.Column("embd_trgt_cnt", sa.Integer, nullable=False, server_default=sa.text("0")),
            sa.Column("embd_cmptn_cnt", sa.Integer, nullable=False, server_default=sa.text("0")),
            sa.Column("aplcn_trgt_dscrp_cn", sa.Text, nullable=True),
            sa.Column("aplcn_trgt_cnfrm_yn", sa.Boolean, nullable=False, server_default=sa.text("false")),
            sa.Column("wrn_stts_msg_cn", sa.Text, nullable=True),
            sa.Column("err_stts_msg_cn", sa.Text, nullable=True),
            sa.Column("creatr_id", sa.String(50), nullable=True),
            sa.Column("crt_dt", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
            sa.Column("mdfcn_dt", sa.DateTime(timezone=True), nullable=True, server_default=sa.func.now()),
            sa.Column("vtlz_dt", sa.DateTime(timezone=True), nullable=True),
            sa.Column("dsbl_dt", sa.DateTime(timezone=True), nullable=True),
            sa.Column("del_dt", sa.DateTime(timezone=True), nullable=True),
        )
        op.create_index("idx_rgltn_status", _REG, ["prcs_stts_cd"])
        # 같은 파일을 두 번 등록하지 않는다(삭제한 판은 제외)
        op.create_index("uq_rgltn_hash_live", _REG, ["file_hash_nm"], unique=True,
                        postgresql_where=sa.text("del_dt IS NULL"))

    if not _has_table(_CLAUSE):
        op.create_table(
            _CLAUSE,
            sa.Column("artcl_id", sa.Uuid(as_uuid=True), **uuid_pk),
            sa.Column("rgltn_id", sa.Uuid(as_uuid=True),
                      sa.ForeignKey(f"{_REG}.rgltn_id", ondelete="CASCADE"), nullable=False),
            sa.Column("artcl_sn", sa.Integer, nullable=False),
            sa.Column("artcl_no_nm", sa.String(50), nullable=False),
            sa.Column("artcl_ttl_nm", sa.String(300), nullable=False, server_default=sa.text("''")),
            sa.Column("chpt_nm", sa.String(300), nullable=False, server_default=sa.text("''")),
            sa.Column("artcl_cn", sa.Text, nullable=False),
            sa.Column("artcl_knd_cd", sa.String(20), nullable=False),
            sa.Column("artcl_knd_src_cd", sa.String(10), nullable=False, server_default=sa.text("'auto'")),
            sa.Column("dsply_yn", sa.Boolean, nullable=False, server_default=sa.text("false")),
            sa.Column("embd_vctr_cn", sa.LargeBinary, nullable=True),
            sa.UniqueConstraint("rgltn_id", "artcl_sn", name="uq_rgltn_artcl_seq"),
        )
        op.create_index("idx_rgltn_artcl_reg", _CLAUSE, ["rgltn_id"])

    if not _has_table(_SENT):
        op.create_table(
            _SENT,
            sa.Column("stc_id", sa.Uuid(as_uuid=True), **uuid_pk),
            sa.Column("artcl_id", sa.Uuid(as_uuid=True),
                      sa.ForeignKey(f"{_CLAUSE}.artcl_id", ondelete="CASCADE"), nullable=False),
            sa.Column("stc_sn", sa.Integer, nullable=False),
            sa.Column("stc_cn", sa.Text, nullable=False),
            sa.Column("lead_yn", sa.Boolean, nullable=False, server_default=sa.text("false")),
            sa.Column("list_grp_sn", sa.Integer, nullable=True),
            sa.Column("embd_vctr_cn", sa.LargeBinary, nullable=True),
            sa.UniqueConstraint("artcl_id", "stc_sn", name="uq_rgltn_stc_seq"),
        )
        op.create_index("idx_rgltn_stc_clause", _SENT, ["artcl_id"])


def downgrade() -> None:
    # 자식 → 부모 순서. 표가 없으면 건너뛴다.
    for table, index in ((_SENT, "idx_rgltn_stc_clause"), (_CLAUSE, "idx_rgltn_artcl_reg")):
        if _has_table(table):
            op.drop_index(index, table_name=table)
            op.drop_table(table)
    if _has_table(_REG):
        op.drop_index("uq_rgltn_hash_live", table_name=_REG)
        op.drop_index("idx_rgltn_status", table_name=_REG)
        op.drop_table(_REG)
