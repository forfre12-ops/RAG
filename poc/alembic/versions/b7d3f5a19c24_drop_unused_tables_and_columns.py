"""안 쓰는 표 4개와 칼럼 18개를 뺀다 — 2026-09-26 API·DB 전수 점검 결과.

왜. 쓰는 코드가 없는 표와, 값을 넣거나 읽는 코드가 없는 칼럼이 남아 있었다.
`poc/scripts/audit_unused_fields.py` 가 ORM 정의·저장소 메서드 호출 인자·응답 모델 생성 지점을
전수로 따라가 낸 목록이고, 같은 커밋에서 이 표·칼럼을 쓰던 ORM 정의와 코드를 함께 걷었다.

표 4개 — 로컬 시험 DB 실측(2026-09-26) 전부 **0행**.

    tad_dm_doc_rqmt_scr_mng   요건별 점수. 행을 만드는 코드가 없다.
    tad_lm_lrn_epoch_mng      학습 에폭 기록. 부르는 곳은 시험뿐이었다(TrainingRepo.log_epoch).
    tad_lm_lrn_datst_mng      학습 문서 목록. 부르는 곳은 시험뿐이었다(register_dataset_rows).
    tad_gm_guide_ver_mng      가이드 문서 업로드·버전 조회 API 전용 — 그 기능을 같은 커밋에서 걷었다.

칼럼 18개.

    tad_dm_doc_mng          ocr_use_yn · otsd_rfrnc_no
    tad_lm_lrn_excn_mng     lrn_nocs · vrfc_nocs · test_nocs · boot_bss_idntfr_nm ·
                            prttn_mth_cd · prttn_seed · flw_excn_id
    tad_mm_mdl_ver_mng      grd_system_hstry_cn · flw_excn_id
    tad_cm_clsf_rslt_mng    tot_mth_cd · infr_req_hr
    tad_dm_doc_lbl_mng      tot_scr
    tad_cm_clsf_bss_mng     rqmt_sn
    tad_sm_syn_doc_mng      doc_id
    tad_lm_llm_usqty_mng    whol_tkn_cnt          (DB 생성열: 입력+출력 토큰 합)
    tad_em_evl_rqmt_mng     rqmt_nm

⚠ 데이터 유실 고지. 값이 들어 있던 것은 다음뿐이다(로컬 시험 DB `koipa-cust` 실측 2026-09-26).

    tad_dm_doc_mng.otsd_rfrnc_no      5행 중 1행 — 시험 업로드에 붙인 외부 문서번호.
                                      엔진은 이 값을 읽는 곳이 없었다.
    tad_cm_clsf_rslt_mng.tot_mth_cd   10행 전부 기본값 'hybrid' — 구분 값으로 쓰인 적이 없다.
    tad_em_evl_rqmt_mng.rqmt_nm       7행 전부 — 시드 마이그레이션이 넣은 요건 이름.
                                      읽는 곳이 없다. 이름은 시드 판(000000000001·c7d8e9f0a1b2)에 남아 있다.
    tad_mm_mdl_ver_mng.grd_system_hstry_cn  1행 — 내용은 JSON null.
    tad_lm_llm_usqty_mng.whol_tkn_cnt 7행 — 다른 두 칼럼의 합이라 잃는 정보가 없다.

  그 밖의 13개 칼럼(ocr_use_yn 은 5행 전부 기본값 false)과 표 4개는 담긴 값이 없다. **운영·고객사 DB 는 이 저장소에서
  볼 수 없어 실측하지 못했다** — 적용 전에 `pg_dump` 를 받아 두는 것을 권한다.

같은 판에서 지우지 않은 것(이유는 점검 보고서 poc/reports/unused_api_db_items_20260926.md):
tad_dm_doc_vctr_mng(유사 문서 조회 — 9/9 고객사 요청으로 pgvector 로 되돌리며 신설한 것) · 값은 채우지만 읽는 곳이 없는 26개 칼럼
(감사·이력 성격 — 점검 때 27개였고 그중 otsd_rfrnc_no 를 이 판에서 지웠다).

안전장치. `DROP` 에 `CASCADE` 를 붙이지 않았다 — 뷰·외래키 같은 의존 객체가 뜻밖에 있으면 조용히
지우지 않고 **판이 멈춘다**(PostgreSQL 은 DDL 도 트랜잭션이라 통째로 되돌아간다). 로컬 시험 DB 에서는
이 표·칼럼을 가리키는 외래키·뷰가 없음을 확인했다(v_monthly_llm_cost 는 whol_tkn_cnt 를 쓰지 않는다).
이미 지운 DB 에서 다시 돌아도 죽지 않는다.

되돌리기. 표 4개와 칼럼 18개를 **빈 채로** 다시 만든다 — 값은 돌아오지 않는다. 다음 두 가지는 원래 값을
알 수 없어 대신 채운다: rqmt_nm 은 요건 코드(rqmt_cd)로, 생성열 whol_tkn_cnt 는 DB 가 다시 계산한다.
DDL 은 9c4e1f7a2b58 시점의 것을 그대로 옮겼다 — 7b3e9d2a4f10(표준 명명)의 downgrade 가 이 표들을
이름으로 찾기 때문에, 되돌리지 않으면 그 아래로 내려가는 길이 끊긴다.

Revision ID: b7d3f5a19c24
Revises: 9c4e1f7a2b58
Create Date: 2026-09-26
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "b7d3f5a19c24"
down_revision = "9c4e1f7a2b58"
branch_labels = None
depends_on = None

# 되돌릴 때 쓴다. 정의는 9c4e1f7a2b58 시점의 DB 에서 그대로 옮겼다.
# (표, 칼럼, 칼럼 정의 — ALTER TABLE ... ADD COLUMN 뒤에 붙는 문장)
COLUMNS: list[tuple[str, str, str]] = [
    ("tad_dm_doc_mng", "ocr_use_yn", "boolean DEFAULT false"),
    ("tad_dm_doc_mng", "otsd_rfrnc_no", "varchar(100)"),
    ("tad_lm_lrn_excn_mng", "lrn_nocs", "integer"),
    ("tad_lm_lrn_excn_mng", "vrfc_nocs", "integer"),
    ("tad_lm_lrn_excn_mng", "test_nocs", "integer"),
    ("tad_lm_lrn_excn_mng", "boot_bss_idntfr_nm", "varchar(100)"),
    ("tad_lm_lrn_excn_mng", "prttn_mth_cd", "varchar(30) DEFAULT 'stratified'"),
    ("tad_lm_lrn_excn_mng", "prttn_seed", "integer"),
    ("tad_lm_lrn_excn_mng", "flw_excn_id", "varchar(64)"),
    ("tad_mm_mdl_ver_mng", "grd_system_hstry_cn", "jsonb"),
    ("tad_mm_mdl_ver_mng", "flw_excn_id", "varchar(64)"),
    ("tad_cm_clsf_rslt_mng", "tot_mth_cd", "varchar(20) DEFAULT 'hybrid'"),
    ("tad_cm_clsf_rslt_mng", "infr_req_hr", "integer"),
    ("tad_dm_doc_lbl_mng", "tot_scr", "numeric(4,2)"),
    ("tad_cm_clsf_bss_mng", "rqmt_sn",
     "integer CONSTRAINT tb_classification_evidence_factor_id_fkey "
     "REFERENCES tad_em_evl_rqmt_mng(rqmt_sn) ON DELETE RESTRICT"),
    ("tad_sm_syn_doc_mng", "doc_id",
     "uuid CONSTRAINT tb_sample_documents_doc_id_fkey REFERENCES tad_dm_doc_mng(doc_id)"),
    ("tad_lm_llm_usqty_mng", "whol_tkn_cnt",
     "integer GENERATED ALWAYS AS (inpt_tkn_cnt + otpt_tkn_cnt) STORED"),
    # NOT NULL 이라 따로 다룬다 — 아래 downgrade() 참조.
    ("tad_em_evl_rqmt_mng", "rqmt_nm", "varchar(100)"),
]

# 위 칼럼에 딸려 있던 인덱스(칼럼을 지우면 함께 사라진다).
INDEXES: list[tuple[str, str]] = [
    ("idx_mv_mlflow", "CREATE INDEX IF NOT EXISTS idx_mv_mlflow ON tad_mm_mdl_ver_mng USING btree (flw_excn_id)"),
]

# 지우는 표 — 서로 참조하지 않고, 이 표들을 가리키는 외래키도 없다.
TABLES = [
    "tad_dm_doc_rqmt_scr_mng",
    "tad_lm_lrn_epoch_mng",
    "tad_lm_lrn_datst_mng",
    "tad_gm_guide_ver_mng",
]

# 되돌릴 때 다시 만드는 표의 DDL(9c4e1f7a2b58 시점). 실행 순서대로 적는다.
TABLE_DDL: list[str] = [
    """
    CREATE TABLE IF NOT EXISTS tad_dm_doc_rqmt_scr_mng (
        doc_id uuid NOT NULL,
        rqmt_sn integer NOT NULL,
        scr numeric(4,2) NOT NULL,
        CONSTRAINT tb_document_factor_scores_pkey PRIMARY KEY (doc_id, rqmt_sn),
        CONSTRAINT ck_dfs_score_0_2 CHECK (scr >= 0 AND scr <= 2),
        CONSTRAINT tb_document_factor_scores_score_check CHECK (scr >= 0 AND scr <= 5),
        CONSTRAINT tb_document_factor_scores_doc_id_fkey FOREIGN KEY (doc_id)
            REFERENCES tad_dm_doc_mng(doc_id) ON DELETE CASCADE,
        CONSTRAINT tb_document_factor_scores_factor_id_fkey FOREIGN KEY (rqmt_sn)
            REFERENCES tad_em_evl_rqmt_mng(rqmt_sn) ON DELETE RESTRICT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tad_lm_lrn_epoch_mng (
        excn_id uuid NOT NULL,
        epoch_sn integer NOT NULL,
        lrn_loss_nvl real,
        vrfc_loss_nvl real,
        vrfc_idct_info_cn jsonb NOT NULL DEFAULT '{}'::jsonb,
        lrnr real,
        rcd_dt timestamptz DEFAULT now(),
        CONSTRAINT tb_training_epochs_pkey PRIMARY KEY (excn_id, epoch_sn),
        CONSTRAINT tb_training_epochs_run_id_fkey FOREIGN KEY (excn_id)
            REFERENCES tad_lm_lrn_excn_mng(excn_id) ON DELETE CASCADE
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS tad_lm_lrn_datst_mng (
        lrn_datst_sn bigint GENERATED ALWAYS AS IDENTITY (SEQUENCE NAME tb_training_datasets_id_seq),
        excn_id uuid NOT NULL,
        doc_id uuid NOT NULL,
        prttn_se_nm varchar(10) NOT NULL,
        grd_sn integer NOT NULL,
        CONSTRAINT tb_training_datasets_pkey PRIMARY KEY (lrn_datst_sn),
        CONSTRAINT tb_training_datasets_run_id_doc_id_key UNIQUE (excn_id, doc_id),
        CONSTRAINT tb_training_datasets_run_id_fkey FOREIGN KEY (excn_id)
            REFERENCES tad_lm_lrn_excn_mng(excn_id) ON DELETE CASCADE,
        CONSTRAINT tb_training_datasets_doc_id_fkey FOREIGN KEY (doc_id)
            REFERENCES tad_dm_doc_mng(doc_id) ON DELETE RESTRICT,
        CONSTRAINT tb_training_datasets_level_id_fkey FOREIGN KEY (grd_sn)
            REFERENCES tad_cm_clsf_grd_mng(grd_sn) ON DELETE RESTRICT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_td_doc ON tad_lm_lrn_datst_mng USING btree (doc_id)",
    "CREATE INDEX IF NOT EXISTS idx_td_run_split ON tad_lm_lrn_datst_mng USING btree (excn_id, prttn_se_nm)",
    """
    CREATE TABLE IF NOT EXISTS tad_gm_guide_ver_mng (
        guide_ver_sn bigint GENERATED ALWAYS AS IDENTITY (SEQUENCE NAME tb_guides_id_seq),
        guide_id varchar(200) NOT NULL,
        guide_ver_nm varchar(50) NOT NULL,
        enfc_dt varchar(30),
        chg_smry_cn text,
        doc_knd_nm varchar(50),
        file_nm varchar(500),
        reg_dt timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT tb_guides_pkey PRIMARY KEY (guide_ver_sn),
        CONSTRAINT uq_guides_id_version UNIQUE (guide_id, guide_ver_nm)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_guides_guide_id ON tad_gm_guide_ver_mng USING btree (guide_id)",
    "CREATE INDEX IF NOT EXISTS idx_guides_registered ON tad_gm_guide_ver_mng USING btree (reg_dt)",
]


def _has_column(conn, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            sa.text(
                "select 1 from information_schema.columns "
                "where table_schema = 'public' and table_name = :t and column_name = :c"
            ),
            {"t": table, "c": column},
        ).first()
    )


def upgrade() -> None:
    conn = op.get_bind()
    # 표부터 — 이 표들은 다른 표를 참조만 하고 참조당하지 않는다.
    for table in TABLES:
        op.execute(sa.text(f"DROP TABLE IF EXISTS {table}"))
    # 칼럼에 딸린 외래키·인덱스는 칼럼과 함께 사라진다(CASCADE 없이도).
    for table, column, _definition in COLUMNS:
        if _has_column(conn, table, column):
            op.execute(sa.text(f"ALTER TABLE {table} DROP COLUMN {column}"))


def downgrade() -> None:
    """빈 채로 되돌린다 — 지워진 값은 돌아오지 않는다(docstring 참조)."""
    conn = op.get_bind()
    for table, column, definition in COLUMNS:
        if not _has_column(conn, table, column):
            op.execute(sa.text(f"ALTER TABLE {table} ADD COLUMN {column} {definition}"))
    # rqmt_nm 은 NOT NULL 이었다. 행이 있으면 바로 걸 수 없어 요건 코드로 채운 뒤 건다.
    op.execute(sa.text("UPDATE tad_em_evl_rqmt_mng SET rqmt_nm = rqmt_cd WHERE rqmt_nm IS NULL"))
    op.execute(sa.text("ALTER TABLE tad_em_evl_rqmt_mng ALTER COLUMN rqmt_nm SET NOT NULL"))
    for _name, ddl in INDEXES:
        op.execute(sa.text(ddl))
    for ddl in TABLE_DDL:
        op.execute(sa.text(ddl))
