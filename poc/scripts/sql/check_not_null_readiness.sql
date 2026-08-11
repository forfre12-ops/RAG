-- KL 운영 DB 사전 점검 — NOT NULL 승격 안전성 확인 (읽기 전용)
--
-- 배경. ORM 모델은 아래 28개 컬럼을 nullable=False 로 선언하는데 DB 는 NULL 을 허용한다.
-- 즉 **DB 가 모델의 전제를 강제하지 않는다**(tb_audit_log.success 같은 감사 필드 포함).
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
-- 참고: 로이드케이 개발 DB 실측(2026-08-11)은 28컬럼 10,285행 전수 NULL 0 이었다.
--       그것은 개발 DB 기준이며 운영 DB 를 대신하지 않는다 — 그래서 이 점검이 필요하다.

WITH counts AS (
  SELECT 'tb_audit_log'::text AS tbl, 'success'::text AS col,
         count(*) FILTER (WHERE success IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_audit_log
  UNION ALL
  SELECT 'tb_classification_evidence'::text AS tbl, 'created_at'::text AS col,
         count(*) FILTER (WHERE created_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_classification_evidence
  UNION ALL
  SELECT 'tb_classification_levels'::text AS tbl, 'is_active'::text AS col,
         count(*) FILTER (WHERE is_active IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_classification_levels
  UNION ALL
  SELECT 'tb_classification_levels'::text AS tbl, 'created_at'::text AS col,
         count(*) FILTER (WHERE created_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_classification_levels
  UNION ALL
  SELECT 'tb_classification_levels'::text AS tbl, 'updated_at'::text AS col,
         count(*) FILTER (WHERE updated_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_classification_levels
  UNION ALL
  SELECT 'tb_classifications'::text AS tbl, 'rag_used'::text AS col,
         count(*) FILTER (WHERE rag_used IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_classifications
  UNION ALL
  SELECT 'tb_classifications'::text AS tbl, 'status'::text AS col,
         count(*) FILTER (WHERE status IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_classifications
  UNION ALL
  SELECT 'tb_classifications'::text AS tbl, 'classified_at'::text AS col,
         count(*) FILTER (WHERE classified_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_classifications
  UNION ALL
  SELECT 'tb_corrections'::text AS tbl, 'corrected_at'::text AS col,
         count(*) FILTER (WHERE corrected_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_corrections
  UNION ALL
  SELECT 'tb_document_labels'::text AS tbl, 'is_verified'::text AS col,
         count(*) FILTER (WHERE is_verified IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_document_labels
  UNION ALL
  SELECT 'tb_document_labels'::text AS tbl, 'labeled_at'::text AS col,
         count(*) FILTER (WHERE labeled_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_document_labels
  UNION ALL
  SELECT 'tb_documents'::text AS tbl, 'metadata'::text AS col,
         count(*) FILTER (WHERE metadata IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_documents
  UNION ALL
  SELECT 'tb_documents'::text AS tbl, 'ocr_used'::text AS col,
         count(*) FILTER (WHERE ocr_used IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_documents
  UNION ALL
  SELECT 'tb_documents'::text AS tbl, 'processing_status'::text AS col,
         count(*) FILTER (WHERE processing_status IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_documents
  UNION ALL
  SELECT 'tb_documents'::text AS tbl, 'uploaded_at'::text AS col,
         count(*) FILTER (WHERE uploaded_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_documents
  UNION ALL
  SELECT 'tb_evaluation_factors'::text AS tbl, 'is_active'::text AS col,
         count(*) FILTER (WHERE is_active IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_evaluation_factors
  UNION ALL
  SELECT 'tb_evaluation_factors'::text AS tbl, 'created_at'::text AS col,
         count(*) FILTER (WHERE created_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_evaluation_factors
  UNION ALL
  SELECT 'tb_evaluation_factors'::text AS tbl, 'updated_at'::text AS col,
         count(*) FILTER (WHERE updated_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_evaluation_factors
  UNION ALL
  SELECT 'tb_level_keywords'::text AS tbl, 'is_active'::text AS col,
         count(*) FILTER (WHERE is_active IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_level_keywords
  UNION ALL
  SELECT 'tb_level_keywords'::text AS tbl, 'created_at'::text AS col,
         count(*) FILTER (WHERE created_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_level_keywords
  UNION ALL
  SELECT 'tb_llm_usage'::text AS tbl, 'success'::text AS col,
         count(*) FILTER (WHERE success IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_llm_usage
  UNION ALL
  SELECT 'tb_model_versions'::text AS tbl, 'is_active'::text AS col,
         count(*) FILTER (WHERE is_active IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_model_versions
  UNION ALL
  SELECT 'tb_model_versions'::text AS tbl, 'created_at'::text AS col,
         count(*) FILTER (WHERE created_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_model_versions
  UNION ALL
  SELECT 'tb_prompt_versions'::text AS tbl, 'created_at'::text AS col,
         count(*) FILTER (WHERE created_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_prompt_versions
  UNION ALL
  SELECT 'tb_sample_documents'::text AS tbl, 'created_at'::text AS col,
         count(*) FILTER (WHERE created_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_sample_documents
  UNION ALL
  SELECT 'tb_training_epochs'::text AS tbl, 'logged_at'::text AS col,
         count(*) FILTER (WHERE logged_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_training_epochs
  UNION ALL
  SELECT 'tb_training_runs'::text AS tbl, 'status'::text AS col,
         count(*) FILTER (WHERE status IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_training_runs
  UNION ALL
  SELECT 'tb_training_runs'::text AS tbl, 'created_at'::text AS col,
         count(*) FILTER (WHERE created_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_training_runs
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
  SELECT 'tb_audit_log'::text AS tbl, 'success'::text AS col,
         count(*) FILTER (WHERE success IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_audit_log
  UNION ALL
  SELECT 'tb_classification_evidence'::text AS tbl, 'created_at'::text AS col,
         count(*) FILTER (WHERE created_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_classification_evidence
  UNION ALL
  SELECT 'tb_classification_levels'::text AS tbl, 'is_active'::text AS col,
         count(*) FILTER (WHERE is_active IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_classification_levels
  UNION ALL
  SELECT 'tb_classification_levels'::text AS tbl, 'created_at'::text AS col,
         count(*) FILTER (WHERE created_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_classification_levels
  UNION ALL
  SELECT 'tb_classification_levels'::text AS tbl, 'updated_at'::text AS col,
         count(*) FILTER (WHERE updated_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_classification_levels
  UNION ALL
  SELECT 'tb_classifications'::text AS tbl, 'rag_used'::text AS col,
         count(*) FILTER (WHERE rag_used IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_classifications
  UNION ALL
  SELECT 'tb_classifications'::text AS tbl, 'status'::text AS col,
         count(*) FILTER (WHERE status IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_classifications
  UNION ALL
  SELECT 'tb_classifications'::text AS tbl, 'classified_at'::text AS col,
         count(*) FILTER (WHERE classified_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_classifications
  UNION ALL
  SELECT 'tb_corrections'::text AS tbl, 'corrected_at'::text AS col,
         count(*) FILTER (WHERE corrected_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_corrections
  UNION ALL
  SELECT 'tb_document_labels'::text AS tbl, 'is_verified'::text AS col,
         count(*) FILTER (WHERE is_verified IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_document_labels
  UNION ALL
  SELECT 'tb_document_labels'::text AS tbl, 'labeled_at'::text AS col,
         count(*) FILTER (WHERE labeled_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_document_labels
  UNION ALL
  SELECT 'tb_documents'::text AS tbl, 'metadata'::text AS col,
         count(*) FILTER (WHERE metadata IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_documents
  UNION ALL
  SELECT 'tb_documents'::text AS tbl, 'ocr_used'::text AS col,
         count(*) FILTER (WHERE ocr_used IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_documents
  UNION ALL
  SELECT 'tb_documents'::text AS tbl, 'processing_status'::text AS col,
         count(*) FILTER (WHERE processing_status IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_documents
  UNION ALL
  SELECT 'tb_documents'::text AS tbl, 'uploaded_at'::text AS col,
         count(*) FILTER (WHERE uploaded_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_documents
  UNION ALL
  SELECT 'tb_evaluation_factors'::text AS tbl, 'is_active'::text AS col,
         count(*) FILTER (WHERE is_active IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_evaluation_factors
  UNION ALL
  SELECT 'tb_evaluation_factors'::text AS tbl, 'created_at'::text AS col,
         count(*) FILTER (WHERE created_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_evaluation_factors
  UNION ALL
  SELECT 'tb_evaluation_factors'::text AS tbl, 'updated_at'::text AS col,
         count(*) FILTER (WHERE updated_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_evaluation_factors
  UNION ALL
  SELECT 'tb_level_keywords'::text AS tbl, 'is_active'::text AS col,
         count(*) FILTER (WHERE is_active IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_level_keywords
  UNION ALL
  SELECT 'tb_level_keywords'::text AS tbl, 'created_at'::text AS col,
         count(*) FILTER (WHERE created_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_level_keywords
  UNION ALL
  SELECT 'tb_llm_usage'::text AS tbl, 'success'::text AS col,
         count(*) FILTER (WHERE success IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_llm_usage
  UNION ALL
  SELECT 'tb_model_versions'::text AS tbl, 'is_active'::text AS col,
         count(*) FILTER (WHERE is_active IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_model_versions
  UNION ALL
  SELECT 'tb_model_versions'::text AS tbl, 'created_at'::text AS col,
         count(*) FILTER (WHERE created_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_model_versions
  UNION ALL
  SELECT 'tb_prompt_versions'::text AS tbl, 'created_at'::text AS col,
         count(*) FILTER (WHERE created_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_prompt_versions
  UNION ALL
  SELECT 'tb_sample_documents'::text AS tbl, 'created_at'::text AS col,
         count(*) FILTER (WHERE created_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_sample_documents
  UNION ALL
  SELECT 'tb_training_epochs'::text AS tbl, 'logged_at'::text AS col,
         count(*) FILTER (WHERE logged_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_training_epochs
  UNION ALL
  SELECT 'tb_training_runs'::text AS tbl, 'status'::text AS col,
         count(*) FILTER (WHERE status IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_training_runs
  UNION ALL
  SELECT 'tb_training_runs'::text AS tbl, 'created_at'::text AS col,
         count(*) FILTER (WHERE created_at IS NULL) AS null_rows, count(*) AS total_rows
    FROM tb_training_runs
)
SELECT CASE WHEN sum(null_rows) = 0 THEN 'SAFE' ELSE 'BLOCKED' END AS "결론",
       count(*) AS "점검 컬럼",
       sum(null_rows) AS "NULL 합계",
       count(*) FILTER (WHERE null_rows > 0) AS "정리 필요 컬럼"
  FROM counts;
