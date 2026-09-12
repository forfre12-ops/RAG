-- KL 운영 DB 사전 점검 — NOT NULL 승격 안전성 확인 (읽기 전용)
--
-- 배경. ORM 모델은 아래 28개 컬럼을 nullable=False 로 선언하는데 DB 는 NULL 을 허용한다.
-- 즉 **DB 가 모델의 전제를 강제하지 않는다**(감사로그 성공여부 tad_am_adt_log_mng.scs_yn 포함).
-- 이를 맞추려면 ALTER ... SET NOT NULL 이 필요한데, 대상 컬럼에 NULL 이 한 건이라도 있으면
-- 마이그레이션이 실패한다. 그래서 **쓰기 전에 먼저 센다.**
--
-- 이 스크립트는 SELECT 만 한다 — 데이터도 스키마도 바꾸지 않는다.
--
-- 실행:
--   psql -U <user> -d <db> -f scripts/sql/check_not_null_readiness.sql
--
-- 판정:
--   verdict = SAFE    전 컬럼 NULL 0 → SET NOT NULL 마이그레이션 가능
--   verdict = BLOCKED NULL 이 있는 컬럼 존재 → 해당 행의 데이터 정리가 선행돼야 한다
--
-- 참고: 한국지식재산보호원 개발 DB 실측(2026-08-11)은 28컬럼 10,285행 전수 NULL 0 이었다.
--       그것은 개발 DB 기준이며 운영 DB 를 대신하지 않는다 — 그래서 이 점검이 필요하다.
--
-- ⚠ [2026-09-12] 이 파일은 **표준 물리명**(tad_*_mng · 표준 약어 칼럼)으로 적혀 있다. 대응표 정본은
--   poc/src/koipa/db/standard_names.py, 개명 판은 migration 7b3e9d2a4f10 이다. 211 과 앞으로의 납품 DB 는
--   모두 개명 뒤이므로 이 점검도 그쪽을 본다.
--   **개명 전 DB(f8a9b0c1d2e3 이하)에서 돌려야 하면 옛 이름 판을 git 이력에서 꺼내 쓴다:**
--       git show 98be2ae0:poc/scripts/sql/check_not_null_readiness.sql
-- ⚠ [2026-09-11 이력] 옛 이름 판을 실제로 돌려 보기 전에는 두 가지가 틀려 있었다 — 이미 지워진 칼럼
--   (분류결과 rag_used)을 찾다 오류로 한 줄도 판정하지 못했고, 5c1d9e0a7b34 가 보는 9개 표 중
--   합성데이터셋구성(멤버십)의 생성일시가 빠져 있었다. 둘 다 고친 뒤 28칸이 됐고, 이 판은 그것을 이어받는다.

WITH counts AS (
  SELECT 'tad_am_adt_log_mng'::text AS tbl, 'scs_yn'::text AS col,
         count(*) FILTER (WHERE scs_yn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_am_adt_log_mng
  UNION ALL
  SELECT 'tad_cm_clsf_bss_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_cm_clsf_bss_mng
  UNION ALL
  SELECT 'tad_cm_clsf_grd_mng'::text AS tbl, 'actvtn_yn'::text AS col,
         count(*) FILTER (WHERE actvtn_yn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_cm_clsf_grd_mng
  UNION ALL
  SELECT 'tad_cm_clsf_grd_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_cm_clsf_grd_mng
  UNION ALL
  SELECT 'tad_cm_clsf_grd_mng'::text AS tbl, 'mdfcn_dt'::text AS col,
         count(*) FILTER (WHERE mdfcn_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_cm_clsf_grd_mng
  UNION ALL
  SELECT 'tad_cm_clsf_rslt_mng'::text AS tbl, 'clsf_stts_nm'::text AS col,
         count(*) FILTER (WHERE clsf_stts_nm IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_cm_clsf_rslt_mng
  UNION ALL
  SELECT 'tad_cm_clsf_rslt_mng'::text AS tbl, 'clsf_dt'::text AS col,
         count(*) FILTER (WHERE clsf_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_cm_clsf_rslt_mng
  UNION ALL
  SELECT 'tad_cm_crct_mng'::text AS tbl, 'crct_dt'::text AS col,
         count(*) FILTER (WHERE crct_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_cm_crct_mng
  UNION ALL
  SELECT 'tad_dm_doc_lbl_mng'::text AS tbl, 'vrfc_cmptn_yn'::text AS col,
         count(*) FILTER (WHERE vrfc_cmptn_yn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_dm_doc_lbl_mng
  UNION ALL
  SELECT 'tad_dm_doc_lbl_mng'::text AS tbl, 'lbl_dt'::text AS col,
         count(*) FILTER (WHERE lbl_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_dm_doc_lbl_mng
  UNION ALL
  SELECT 'tad_dm_doc_mng'::text AS tbl, 'mtdt_dsctn'::text AS col,
         count(*) FILTER (WHERE mtdt_dsctn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_dm_doc_mng
  UNION ALL
  SELECT 'tad_dm_doc_mng'::text AS tbl, 'ocr_use_yn'::text AS col,
         count(*) FILTER (WHERE ocr_use_yn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_dm_doc_mng
  UNION ALL
  SELECT 'tad_dm_doc_mng'::text AS tbl, 'prcs_stts_nm'::text AS col,
         count(*) FILTER (WHERE prcs_stts_nm IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_dm_doc_mng
  UNION ALL
  SELECT 'tad_dm_doc_mng'::text AS tbl, 'uld_dt'::text AS col,
         count(*) FILTER (WHERE uld_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_dm_doc_mng
  UNION ALL
  SELECT 'tad_em_evl_rqmt_mng'::text AS tbl, 'actvtn_yn'::text AS col,
         count(*) FILTER (WHERE actvtn_yn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_em_evl_rqmt_mng
  UNION ALL
  SELECT 'tad_em_evl_rqmt_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_em_evl_rqmt_mng
  UNION ALL
  SELECT 'tad_em_evl_rqmt_mng'::text AS tbl, 'mdfcn_dt'::text AS col,
         count(*) FILTER (WHERE mdfcn_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_em_evl_rqmt_mng
  UNION ALL
  SELECT 'tad_gm_grd_kywd_mng'::text AS tbl, 'actvtn_yn'::text AS col,
         count(*) FILTER (WHERE actvtn_yn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_gm_grd_kywd_mng
  UNION ALL
  SELECT 'tad_gm_grd_kywd_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_gm_grd_kywd_mng
  UNION ALL
  SELECT 'tad_lm_llm_usqty_mng'::text AS tbl, 'scs_yn'::text AS col,
         count(*) FILTER (WHERE scs_yn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_lm_llm_usqty_mng
  UNION ALL
  SELECT 'tad_mm_mdl_ver_mng'::text AS tbl, 'actvtn_yn'::text AS col,
         count(*) FILTER (WHERE actvtn_yn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_mm_mdl_ver_mng
  UNION ALL
  SELECT 'tad_mm_mdl_ver_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_mm_mdl_ver_mng
  UNION ALL
  SELECT 'tad_pm_prmpt_ver_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_pm_prmpt_ver_mng
  UNION ALL
  SELECT 'tad_sm_syn_doc_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_sm_syn_doc_mng
  UNION ALL
  SELECT 'tad_sm_syn_datst_cpst_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_sm_syn_datst_cpst_mng
  UNION ALL
  SELECT 'tad_lm_lrn_epoch_mng'::text AS tbl, 'rcd_dt'::text AS col,
         count(*) FILTER (WHERE rcd_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_lm_lrn_epoch_mng
  UNION ALL
  SELECT 'tad_lm_lrn_excn_mng'::text AS tbl, 'lrn_excn_stts_cd'::text AS col,
         count(*) FILTER (WHERE lrn_excn_stts_cd IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_lm_lrn_excn_mng
  UNION ALL
  SELECT 'tad_lm_lrn_excn_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_lm_lrn_excn_mng
)
SELECT tbl AS "테이블",
       col AS "컬럼",
       null_rows AS "NULL 행",
       total_rows AS "전체 행",
       CASE WHEN null_rows = 0 THEN 'OK' ELSE 'NEEDS_FIX' END AS "판정"
  FROM counts
 ORDER BY null_rows DESC, tbl, col;

-- 한 줄 요약 — 위 표가 길면 이것만 봐도 된다.
WITH counts AS (
  SELECT 'tad_am_adt_log_mng'::text AS tbl, 'scs_yn'::text AS col,
         count(*) FILTER (WHERE scs_yn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_am_adt_log_mng
  UNION ALL
  SELECT 'tad_cm_clsf_bss_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_cm_clsf_bss_mng
  UNION ALL
  SELECT 'tad_cm_clsf_grd_mng'::text AS tbl, 'actvtn_yn'::text AS col,
         count(*) FILTER (WHERE actvtn_yn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_cm_clsf_grd_mng
  UNION ALL
  SELECT 'tad_cm_clsf_grd_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_cm_clsf_grd_mng
  UNION ALL
  SELECT 'tad_cm_clsf_grd_mng'::text AS tbl, 'mdfcn_dt'::text AS col,
         count(*) FILTER (WHERE mdfcn_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_cm_clsf_grd_mng
  UNION ALL
  SELECT 'tad_cm_clsf_rslt_mng'::text AS tbl, 'clsf_stts_nm'::text AS col,
         count(*) FILTER (WHERE clsf_stts_nm IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_cm_clsf_rslt_mng
  UNION ALL
  SELECT 'tad_cm_clsf_rslt_mng'::text AS tbl, 'clsf_dt'::text AS col,
         count(*) FILTER (WHERE clsf_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_cm_clsf_rslt_mng
  UNION ALL
  SELECT 'tad_cm_crct_mng'::text AS tbl, 'crct_dt'::text AS col,
         count(*) FILTER (WHERE crct_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_cm_crct_mng
  UNION ALL
  SELECT 'tad_dm_doc_lbl_mng'::text AS tbl, 'vrfc_cmptn_yn'::text AS col,
         count(*) FILTER (WHERE vrfc_cmptn_yn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_dm_doc_lbl_mng
  UNION ALL
  SELECT 'tad_dm_doc_lbl_mng'::text AS tbl, 'lbl_dt'::text AS col,
         count(*) FILTER (WHERE lbl_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_dm_doc_lbl_mng
  UNION ALL
  SELECT 'tad_dm_doc_mng'::text AS tbl, 'mtdt_dsctn'::text AS col,
         count(*) FILTER (WHERE mtdt_dsctn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_dm_doc_mng
  UNION ALL
  SELECT 'tad_dm_doc_mng'::text AS tbl, 'ocr_use_yn'::text AS col,
         count(*) FILTER (WHERE ocr_use_yn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_dm_doc_mng
  UNION ALL
  SELECT 'tad_dm_doc_mng'::text AS tbl, 'prcs_stts_nm'::text AS col,
         count(*) FILTER (WHERE prcs_stts_nm IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_dm_doc_mng
  UNION ALL
  SELECT 'tad_dm_doc_mng'::text AS tbl, 'uld_dt'::text AS col,
         count(*) FILTER (WHERE uld_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_dm_doc_mng
  UNION ALL
  SELECT 'tad_em_evl_rqmt_mng'::text AS tbl, 'actvtn_yn'::text AS col,
         count(*) FILTER (WHERE actvtn_yn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_em_evl_rqmt_mng
  UNION ALL
  SELECT 'tad_em_evl_rqmt_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_em_evl_rqmt_mng
  UNION ALL
  SELECT 'tad_em_evl_rqmt_mng'::text AS tbl, 'mdfcn_dt'::text AS col,
         count(*) FILTER (WHERE mdfcn_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_em_evl_rqmt_mng
  UNION ALL
  SELECT 'tad_gm_grd_kywd_mng'::text AS tbl, 'actvtn_yn'::text AS col,
         count(*) FILTER (WHERE actvtn_yn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_gm_grd_kywd_mng
  UNION ALL
  SELECT 'tad_gm_grd_kywd_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_gm_grd_kywd_mng
  UNION ALL
  SELECT 'tad_lm_llm_usqty_mng'::text AS tbl, 'scs_yn'::text AS col,
         count(*) FILTER (WHERE scs_yn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_lm_llm_usqty_mng
  UNION ALL
  SELECT 'tad_mm_mdl_ver_mng'::text AS tbl, 'actvtn_yn'::text AS col,
         count(*) FILTER (WHERE actvtn_yn IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_mm_mdl_ver_mng
  UNION ALL
  SELECT 'tad_mm_mdl_ver_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_mm_mdl_ver_mng
  UNION ALL
  SELECT 'tad_pm_prmpt_ver_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_pm_prmpt_ver_mng
  UNION ALL
  SELECT 'tad_sm_syn_doc_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_sm_syn_doc_mng
  UNION ALL
  SELECT 'tad_sm_syn_datst_cpst_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_sm_syn_datst_cpst_mng
  UNION ALL
  SELECT 'tad_lm_lrn_epoch_mng'::text AS tbl, 'rcd_dt'::text AS col,
         count(*) FILTER (WHERE rcd_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_lm_lrn_epoch_mng
  UNION ALL
  SELECT 'tad_lm_lrn_excn_mng'::text AS tbl, 'lrn_excn_stts_cd'::text AS col,
         count(*) FILTER (WHERE lrn_excn_stts_cd IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_lm_lrn_excn_mng
  UNION ALL
  SELECT 'tad_lm_lrn_excn_mng'::text AS tbl, 'crt_dt'::text AS col,
         count(*) FILTER (WHERE crt_dt IS NULL) AS null_rows, count(*) AS total_rows
    FROM tad_lm_lrn_excn_mng
)
SELECT CASE WHEN sum(null_rows) = 0 THEN 'SAFE' ELSE 'BLOCKED' END AS "결론",
       count(*) AS "점검 컬럼",
       sum(null_rows) AS "NULL 합계",
       count(*) FILTER (WHERE null_rows > 0) AS "정리 필요 컬럼"
  FROM counts;
