"""표준 명명 — AI 솔루션 DB 의 표·칼럼 이름을 KOIPA 표준용어집 이름으로 바꾼다.

Revision ID: 7b3e9d2a4f10
Revises: 5c1d9e0a7b34
Create Date: 2026-09-11

감리 인쇄 71쪽 (다): 「데이터베이스설계서(AI 솔루션)」 표준 준수 미흡. 설계서의 물리명은
실제 DB 와 같아야 하므로 실제 이름을 바꾼다. 대응의 정본은 koipa/db/standard_names.py 이고,
아래 표는 그 **사본**이다 — 마이그레이션은 앱 코드가 나중에 바뀌어도 같은 일을 해야 하므로
import 하지 않는다.

순서와 이유:
  1) 칼럼 이름 — 옛 표 이름으로 찾는다. 파티션 부모의 칼럼 이름은 PostgreSQL 이 자식에게 전파한다.
     생성 칼럼(active_key·total_tokens)·뷰(v_monthly_llm_cost)·인덱스·제약·FK 는 칼럼을 번호로
     붙들고 있어 이름을 따라간다.
  2) 파티션 자식 — services/partitions.py 는 `부모_YYYY_MM` 로 자식을 만든다. 부모만 바꾸면
     다음 틱이 새 이름으로 이미 있는 월을 다시 만들려다 범위가 겹쳐 실패한다. 자식도 같이 바꾼다.
     자식 목록은 pg_inherits 에서 읽는다 — 운영 DB 는 beat 가 만든 월이 더 있다.
  3) 표 이름.
  4) update_timestamp() — plpgsql 본문은 칼럼을 **글자로** 적는다(NEW.updated_at). 이름을 따라가지
     않아 다시 만든다. 안 하면 등급·요건 표 UPDATE 가 실행 시점에 죽는다.

바꾸지 않는 것: 인덱스·제약·시퀀스 이름(표준용어집 대상이 아니다), 형식(타입), 뷰 이름.
데이터는 한 행도 움직이지 않는다 — 전부 이름 변경이다.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7b3e9d2a4f10"
down_revision: Union[str, None] = "5c1d9e0a7b34"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# 옛 표 → 표준 표
TABLES = {
    "tb_audit_log": "tad_am_adt_log_mng",
    "tb_chunks": "tad_cm_chnk_mng",
    "tb_classification_evidence": "tad_cm_clsf_bss_mng",
    "tb_classification_levels": "tad_cm_clsf_grd_mng",
    "tb_classifications": "tad_cm_clsf_rslt_mng",
    "tb_corrections": "tad_cm_crct_mng",
    "tb_document_factor_scores": "tad_dm_doc_rqmt_scr_mng",
    "tb_document_labels": "tad_dm_doc_lbl_mng",
    "tb_documents": "tad_dm_doc_mng",
    "tb_evaluation_factors": "tad_em_evl_rqmt_mng",
    "tb_level_keywords": "tad_gm_grd_kywd_mng",
    "tb_guides": "tad_gm_guide_ver_mng",
    "tb_llm_usage": "tad_lm_llm_usqty_mng",
    "tb_training_datasets": "tad_lm_lrn_datst_mng",
    "tb_training_epochs": "tad_lm_lrn_epoch_mng",
    "tb_training_runs": "tad_lm_lrn_excn_mng",
    "tb_model_versions": "tad_mm_mdl_ver_mng",
    "tb_prompt_versions": "tad_pm_prmpt_ver_mng",
    "tb_sample_documents": "tad_sm_syn_doc_mng",
    "tb_document_vectors": "tad_dm_doc_vctr_mng",
    "tb_sample_dataset_membership": "tad_sm_syn_datst_cpst_mng",
    "tb_advisory_locks": "tad_sy_lck_mng",
}

# 옛 표 → ((옛 칼럼, 표준 칼럼), ...) — 이름이 바뀌는 칼럼만.
COLUMNS = {
    "tb_audit_log": (
        ("audit_id", "adt_sn"),
        ("request_id", "dmnd_id"),
        ("actor_id", "actr_id"),
        ("actor_role", "actr_role_nm"),
        ("action", "flfmt_bhvr_cd"),
        ("target_type", "trgt_type_cd"),
        ("target_id", "trgt_id"),
        ("payload_hash", "dmnd_mtxt_hash_cn"),
        ("ip_address", "dmnd_ip_addr"),
        ("user_agent", "user_agnt_cn"),
        ("success", "scs_yn"),
        ("error_code", "err_cd"),
        ("occurred_at", "ocrn_dt"),
    ),
    "tb_chunks": (
        ("chunk_id", "chnk_id"),
        ("chunk_index", "chnk_no"),
        ("content", "chnk_cn"),
        ("token_count", "tkn_cnt"),
        ("char_count", "char_cnt"),
        ("section_path", "sctn_path_nm"),
        ("overlap_prev", "prev_chnk_ovlp_tkn_cnt"),
        ("overlap_next", "next_chnk_ovlp_tkn_cnt"),
        ("created_at", "crt_dt"),
    ),
    "tb_classification_evidence": (
        ("evidence_id", "bss_sn"),
        ("classification_id", "clsf_id"),
        ("chunk_id", "chnk_id"),
        ("evidence_type", "bss_type_nm"),
        ("factor_id", "rqmt_sn"),
        ("excerpt", "exct_cn"),
        ("excerpt_start", "exct_bgng_pstn_nm"),
        ("excerpt_end", "exct_end_pstn_nm"),
        ("contribution", "cndg_nvl"),
        ("created_at", "crt_dt"),
    ),
    "tb_classification_levels": (
        ("level_id", "grd_sn"),
        ("level_code", "grd_cd"),
        ("level_name", "grd_nm"),
        ("level_order", "grd_seq"),
        ("description", "clsf_grd_expln"),
        ("color_hex", "colr_cd"),
        ("loss_weight", "loss_wgvl_cfc"),
        ("is_active", "actvtn_yn"),
        ("created_at", "crt_dt"),
        ("updated_at", "mdfcn_dt"),
        ("created_by", "creatr_id"),
    ),
    "tb_classifications": (
        ("classification_id", "clsf_id"),
        ("model_version", "mdl_ver_nm"),
        ("predicted_level_id", "predc_grd_sn"),
        ("confidence", "rlbl_scr"),
        ("alternatives", "nxtrnk_grd_list_cn"),
        ("automation_assessment", "auto_cfmtn_evl_info_cn"),
        ("aggregation_method", "tot_mth_cd"),
        ("chunk_count", "chnk_cnt"),
        ("status", "clsf_stts_nm"),
        ("initial_status", "clsf_frst_stts_nm"),
        ("inference_ms", "infr_req_hr"),
        ("classified_at", "clsf_dt"),
    ),
    "tb_corrections": (
        ("correction_id", "crct_sn"),
        ("classification_id", "clsf_id"),
        ("original_level_id", "orgnl_grd_sn"),
        ("corrected_level_id", "cfmtn_grd_sn"),
        ("direction", "crct_ornt_nm"),
        ("reason", "crct_rsn"),
        ("corrected_by", "clbtr_id"),
        ("corrected_at", "crct_dt"),
        ("consumed_in_run", "rflt_lrn_excn_id"),
        ("consumed_at", "rflt_dt"),
    ),
    "tb_document_factor_scores": (
        ("factor_id", "rqmt_sn"),
        ("score", "scr"),
    ),
    "tb_document_labels": (
        ("level_id", "grd_sn"),
        ("labeled_by", "lbl_mnbd_nm"),
        ("labeler_id", "lbl_wrtr_id"),
        ("confidence", "rlbl_scr"),
        ("total_score", "tot_scr"),
        ("notes", "memo_dtl_cn"),
        ("is_verified", "vrfc_cmptn_yn"),
        ("verified_by", "vrfr_id"),
        ("labeled_at", "lbl_dt"),
        ("verified_at", "vrfc_dt"),
    ),
    "tb_documents": (
        ("external_ref", "otsd_rfrnc_no"),
        ("filename", "file_nm"),
        ("source_format", "orgnl_frmat_nm"),
        ("file_size_bytes", "file_sz"),
        ("file_hash", "file_hash_nm"),
        ("metadata", "mtdt_dsctn"),
        ("raw_text_uri", "orgtxt_path_nm"),
        ("normalized_text_uri", "nrmlz_txt_path_nm"),
        ("text_preview", "mtxt_prvw_cn"),
        ("char_count", "char_cnt"),
        ("extraction_method", "extr_mth_nm"),
        ("extraction_quality", "extr_qlty_scr"),
        ("ocr_used", "ocr_use_yn"),
        ("processing_status", "prcs_stts_nm"),
        ("error_message", "err_stts_msg_cn"),
        ("uploaded_at", "uld_dt"),
        ("processed_at", "prcs_cmptn_dt"),
        ("created_by", "creatr_id"),
        ("deleted_at", "del_dt"),
    ),
    "tb_evaluation_factors": (
        ("factor_id", "rqmt_sn"),
        ("factor_code", "rqmt_cd"),
        ("factor_name", "rqmt_nm"),
        ("description", "evl_rqmt_expln"),
        ("weight", "wgvl_cfc"),
        ("is_active", "actvtn_yn"),
        ("created_at", "crt_dt"),
        ("updated_at", "mdfcn_dt"),
    ),
    "tb_level_keywords": (
        ("keyword_id", "kywd_sn"),
        ("level_id", "grd_sn"),
        ("keyword", "kywd_nm"),
        ("pattern_type", "ptn_type_nm"),
        ("factor_id", "rqmt_sn"),
        ("weight", "wgvl_cfc"),
        ("source", "src_nm"),
        ("is_active", "actvtn_yn"),
        ("created_at", "crt_dt"),
    ),
    "tb_guides": (
        ("id", "guide_ver_sn"),
        ("version", "guide_ver_nm"),
        ("effective_date", "enfc_dt"),
        ("change_summary", "chg_smry_cn"),
        ("doc_type", "doc_knd_nm"),
        ("filename", "file_nm"),
        ("registered_at", "reg_dt"),
    ),
    "tb_llm_usage": (
        ("usage_id", "use_rcd_sn"),
        ("provider", "offr_id"),
        ("model", "mdl_nm"),
        ("purpose", "clot_prps"),
        ("reference_type", "rfrnc_trgt_type_cd"),
        ("reference_id", "rfrnc_trgt_id"),
        ("input_tokens", "inpt_tkn_cnt"),
        ("output_tokens", "otpt_tkn_cnt"),
        ("total_tokens", "whol_tkn_cnt"),
        ("cost_usd", "usd_cst"),
        ("cost_krw", "kcur_cst"),
        ("billing_phase", "bllng_se_cd"),
        ("latency_ms", "rspns_dly_hr"),
        ("success", "scs_yn"),
        ("error_code", "err_cd"),
        ("called_at", "clot_dt"),
    ),
    "tb_training_datasets": (
        ("id", "lrn_datst_sn"),
        ("run_id", "excn_id"),
        ("split_type", "prttn_se_nm"),
        ("level_id", "grd_sn"),
    ),
    "tb_training_epochs": (
        ("run_id", "excn_id"),
        ("epoch", "epoch_sn"),
        ("train_loss", "lrn_loss_nvl"),
        ("val_loss", "vrfc_loss_nvl"),
        ("val_metrics", "vrfc_idct_info_cn"),
        ("learning_rate", "lrnr"),
        ("logged_at", "rcd_dt"),
    ),
    "tb_training_runs": (
        ("run_id", "excn_id"),
        ("model_version_id", "mdl_ver_id"),
        ("mlflow_run_id", "flw_excn_id"),
        ("status", "lrn_excn_stts_cd"),
        ("started_at", "bgng_dt"),
        ("completed_at", "end_dt"),
        ("duration_sec", "req_hr"),
        ("total_samples", "whol_sample_cnt"),
        ("train_count", "lrn_nocs"),
        ("val_count", "vrfc_nocs"),
        ("test_count", "test_nocs"),
        ("split_method", "prttn_mth_cd"),
        ("split_seed", "prttn_seed"),
        ("hyperparameters", "hprprm_info_cn"),
        ("final_metrics", "last_idct_info_cn"),
        ("trigger_type", "boot_mth_cd"),
        ("trigger_ref", "boot_bss_idntfr_nm"),
        ("error_message", "err_stts_msg_cn"),
        ("created_at", "crt_dt"),
        ("created_by", "creatr_id"),
    ),
    "tb_model_versions": (
        ("version_id", "mdl_ver_id"),
        ("version_label", "ver_lbl_nm"),
        ("base_model", "base_mdl_nm"),
        ("model_type", "mdl_type_nm"),
        ("trained_at", "lrn_cmptn_dt"),
        ("training_run_id", "lrn_excn_id"),
        ("training_data_count", "lrn_data_nocs"),
        ("metrics", "idct_info_cn"),
        ("model_uri", "mdl_strg_path_nm"),
        ("mlflow_run_id", "flw_excn_id"),
        ("is_active", "actvtn_yn"),
        ("active_key", "actvtn_key"),
        ("activated_at", "vtlz_dt"),
        ("deactivated_at", "dsbl_dt"),
        ("rolled_back_from", "rlbk_src_ver_id"),
        ("rollback_reason", "rlbk_rsn"),
        ("level_snapshot", "grd_system_hstry_cn"),
        ("created_at", "crt_dt"),
    ),
    "tb_prompt_versions": (
        ("prompt_version", "prmpt_ver_nm"),
        ("chain_stage", "crt_stp_nm"),
        ("template", "tmplt_cn"),
        ("created_at", "crt_dt"),
        ("created_by", "creatr_id"),
        ("notes", "prmpt_ver_memo_cn"),
    ),
    "tb_sample_documents": (
        ("sample_id", "syn_doc_id"),
        ("target_level_id", "goal_grd_sn"),
        ("corrected_level_id", "cfmtn_grd_sn"),
        ("doc_type", "doc_knd_nm"),
        ("outline_prompt_version", "otln_prmpt_ver_nm"),
        ("body_prompt_version", "mtxt_prmpt_ver_nm"),
        ("qc_prompt_version", "qlty_insp_prmpt_ver_nm"),
        ("llm_provider", "llm_offr_nm"),
        ("llm_model", "llm_mdl_nm"),
        ("generated_outline", "crt_otln"),
        ("generated_content", "crt_mtxt_cn"),
        ("quality_score", "qlty_scr"),
        ("quality_report", "qlty_insp_rptp_cn"),
        ("label_source", "lbl_src_nm"),
        ("parse_error", "prsng_err_rsn"),
        ("review_status", "igi_stts_cd"),
        ("reviewed_by", "chckr_id"),
        ("reviewed_at", "igi_dt"),
        ("rejection_reason", "rjct_rsn"),
        ("created_at", "crt_dt"),
    ),
    "tb_document_vectors": (
        ("embedding", "embd_vctr_cn"),
        ("model", "embd_mdl_nm"),
        ("chunk_count", "chnk_cnt"),
        ("content_sha256", "mtxt_hash_cn"),
        ("created_at", "crt_dt"),
    ),
    "tb_sample_dataset_membership": (
        ("membership_id", "cpst_sn"),
        ("sample_id", "syn_doc_id"),
        ("dataset_version", "datst_ver_nm"),
        ("synth_job_id", "syn_job_id"),
        ("created_at", "crt_dt"),
    ),
    "tb_advisory_locks": (
        ("name", "lck_nm"),
    ),
}

PARTITION_PARENTS = ("tb_chunks", "tb_llm_usage", "tb_audit_log")

_UPDATE_TIMESTAMP = """
CREATE OR REPLACE FUNCTION update_timestamp() RETURNS TRIGGER AS $$
BEGIN NEW.{col} = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;
"""


def _children(bind, parent: str) -> list[str]:
    rows = bind.execute(
        sa.text(
            "SELECT c.relname FROM pg_inherits i "
            "JOIN pg_class c ON c.oid = i.inhrelid "
            "JOIN pg_class p ON p.oid = i.inhparent "
            "WHERE p.relname = :p ORDER BY c.relname"
        ),
        {"p": parent},
    )
    return [r[0] for r in rows]


def _rename_partitions(bind, old_parent: str, new_parent: str) -> None:
    for child in _children(bind, old_parent):
        if not child.startswith(old_parent + "_"):
            raise RuntimeError(f"파티션 자식 이름이 부모 접두로 시작하지 않는다: {child}")
        op.execute(f"ALTER TABLE {child} RENAME TO {new_parent}{child[len(old_parent):]}")


def upgrade() -> None:
    bind = op.get_bind()
    for table, cols in COLUMNS.items():
        for old, new in cols:
            op.execute(f"ALTER TABLE {table} RENAME COLUMN {old} TO {new}")
    for parent in PARTITION_PARENTS:
        _rename_partitions(bind, parent, TABLES[parent])
    for old, new in TABLES.items():
        op.execute(f"ALTER TABLE {old} RENAME TO {new}")
    op.execute(_UPDATE_TIMESTAMP.format(col="mdfcn_dt"))


def downgrade() -> None:
    bind = op.get_bind()
    op.execute(_UPDATE_TIMESTAMP.format(col="updated_at"))
    # 자식 먼저 — 부모 이름으로 자식을 찾으므로, 부모를 먼저 되돌리면 새 이름 부모가 없어
    # 자식이 tad_ 이름으로 남는다(첫 판이 그랬다 · 2026-09-11 왕복 시험).
    for parent in PARTITION_PARENTS:
        _rename_partitions(bind, TABLES[parent], parent)
    for old, new in TABLES.items():
        op.execute(f"ALTER TABLE {new} RENAME TO {old}")
    for table, cols in COLUMNS.items():
        for old, new in cols:
            op.execute(f"ALTER TABLE {table} RENAME COLUMN {new} TO {old}")
