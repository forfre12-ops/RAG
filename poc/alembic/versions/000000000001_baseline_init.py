"""baseline_init: 단일 진실 소스 (init.sql 일원화).

Revision ID: 000000000001
Revises:
Create Date: 2026-05-28

목적:
- 이전 흐름: `psql -f migrations/init.sql && alembic upgrade head`
  → init.sql과 alembic 양쪽을 model 변경마다 동시에 손봐야 했음 (이중 유지).
- 본 baseline에서 init.sql v2의 스키마 전체(테이블 19, 인덱스 30+, 확장 2,
  파티션 子 11, 트리거 3, 함수 2, 뷰 4, 시드 3 그룹)를 alembic이 단독 적용.
- `poc/migrations/init.sql`은 `.deprecated`로 동결 — 더 이상 사용 안 함.

idempotency:
- 모든 DDL은 `IF NOT EXISTS`/`OR REPLACE` 패턴으로 작성.
- 기존 production DB(=init.sql로 이미 부트스트랩된 환경)에서 `alembic upgrade head`
  실행 시에도 안전. `alembic stamp 000000000001` 한 번이면 충분하며,
  실제 upgrade가 다시 돌아도 NO-OP.

체인:
  000000000001 (본 baseline)
    └─ 80d75521b95a (구 baseline; 본 마이그레이션 추가로 NO-OP 의미만 유지)
        └─ a1f2c3d4e5f6 (N4 hot-path indexes)
"""
from typing import Sequence, Union

from alembic import op


revision: str = "000000000001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ============================================================
# DDL block — init.sql v2 1:1 이식 (IF NOT EXISTS 보강)
# ============================================================

_DDL = r"""
-- ============================================================
-- 확장
-- ============================================================
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- [A] 테넌트
-- ============================================================
CREATE TABLE IF NOT EXISTS tenants (
    tenant_id           VARCHAR(50)     PRIMARY KEY,
    name                VARCHAR(200)    NOT NULL,
    rag_enabled         BOOLEAN         DEFAULT TRUE,
    active_model_version VARCHAR(50),
    api_key_hash        VARCHAR(128),
    metadata            JSONB           DEFAULT '{}',
    is_active           BOOLEAN         DEFAULT TRUE,
    created_at          TIMESTAMPTZ     DEFAULT NOW()
);

INSERT INTO tenants (tenant_id, name) VALUES ('default', '기본 테넌트')
ON CONFLICT DO NOTHING;

-- ============================================================
-- [B] 등급체계
-- ============================================================
CREATE TABLE IF NOT EXISTS classification_levels (
    level_id        SERIAL          PRIMARY KEY,
    level_code      VARCHAR(20)     NOT NULL UNIQUE,
    level_name      VARCHAR(50)     NOT NULL,
    level_order     SMALLINT        NOT NULL,
    description     TEXT,
    color_hex       VARCHAR(7)      DEFAULT '#808080',
    loss_weight     DECIMAL(4,2)    DEFAULT 1.0,
    is_active       BOOLEAN         DEFAULT TRUE,
    created_at      TIMESTAMPTZ     DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     DEFAULT NOW(),
    created_by      VARCHAR(50)
);

CREATE INDEX IF NOT EXISTS idx_cl_active_order
  ON classification_levels(is_active, level_order);

INSERT INTO classification_levels (level_code, level_name, level_order, color_hex, loss_weight) VALUES
  ('TS', '특급기밀',   1, '#7B1FA2', 3.0),
  ('S1', '1급 비밀',   2, '#D32F2F', 2.0),
  ('S2', '2급 대외비', 3, '#F57C00', 1.0),
  ('S3', '3급 공개',   4, '#388E3C', 1.0)
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS evaluation_factors (
    factor_id       SERIAL          PRIMARY KEY,
    factor_code     VARCHAR(30)     NOT NULL UNIQUE,
    factor_name     VARCHAR(100)    NOT NULL,
    description     TEXT,
    weight          DECIMAL(3,2)    NOT NULL DEFAULT 0.25,
    is_active       BOOLEAN         DEFAULT TRUE,
    created_at      TIMESTAMPTZ     DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     DEFAULT NOW()
);

INSERT INTO evaluation_factors (factor_code, factor_name, weight) VALUES
  ('ECONOMIC_VALUE',   '경제적 가치',       0.30),
  ('NON_PUBLICITY',    '비공지성',          0.25),
  ('MANAGEMENT_LEVEL', '관리수준',          0.15),
  ('LEAK_IMPACT',      '유출 시 영향도',    0.30)
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS level_keywords (
    keyword_id      SERIAL          PRIMARY KEY,
    level_id        INT             NOT NULL REFERENCES classification_levels(level_id) ON DELETE RESTRICT,
    keyword         VARCHAR(200)    NOT NULL,
    pattern_type    VARCHAR(20)     NOT NULL DEFAULT 'exact',
    factor_id       INT             REFERENCES evaluation_factors(factor_id) ON DELETE RESTRICT,
    weight          DECIMAL(3,2)    DEFAULT 1.0,
    source          VARCHAR(30)     DEFAULT 'manual',
    example_context TEXT,
    is_active       BOOLEAN         DEFAULT TRUE,
    created_at      TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_lk_level_active ON level_keywords(level_id, is_active);
CREATE INDEX IF NOT EXISTS idx_lk_keyword_trgm ON level_keywords USING gin (keyword gin_trgm_ops);

-- ============================================================
-- [C] 문서
-- ============================================================
CREATE TABLE IF NOT EXISTS documents (
    doc_id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           VARCHAR(50)     NOT NULL REFERENCES tenants(tenant_id),
    external_ref        VARCHAR(100),
    filename            VARCHAR(500)    NOT NULL,
    source_format       VARCHAR(10)     NOT NULL,
    file_size_bytes     BIGINT,
    file_hash           VARCHAR(64),
    metadata            JSONB           DEFAULT '{}',
    raw_text_uri        VARCHAR(500),
    normalized_text_uri VARCHAR(500),
    text_preview        VARCHAR(2000),
    char_count          INT,
    extraction_method   VARCHAR(30)     DEFAULT 'parser',
    extraction_quality  DECIMAL(3,2),
    ocr_used            BOOLEAN         DEFAULT FALSE,
    processing_status   VARCHAR(20)     DEFAULT 'pending',
    error_message       TEXT,
    uploaded_at         TIMESTAMPTZ     DEFAULT NOW(),
    processed_at        TIMESTAMPTZ,
    created_by          VARCHAR(50)
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_doc_hash
  ON documents(tenant_id, file_hash) WHERE file_hash IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_doc_tenant_status ON documents(tenant_id, processing_status);
CREATE INDEX IF NOT EXISTS idx_doc_format ON documents(source_format);
CREATE INDEX IF NOT EXISTS idx_doc_metadata ON documents USING gin (metadata jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_doc_uploaded ON documents(uploaded_at DESC);
CREATE INDEX IF NOT EXISTS idx_doc_pending
  ON documents(uploaded_at) WHERE processing_status = 'pending';

-- 청크: 멀티 테넌트 + 월별 파티셔닝
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id        UUID            DEFAULT gen_random_uuid(),
    doc_id          UUID            NOT NULL,
    tenant_id       VARCHAR(50)     NOT NULL,
    chunk_index     INT             NOT NULL,
    content         TEXT            NOT NULL,
    token_count     INT             NOT NULL,
    char_count      INT             NOT NULL,
    page_start      SMALLINT,
    page_end        SMALLINT,
    section_path    TEXT[],
    overlap_prev    SMALLINT        DEFAULT 0,
    overlap_next    SMALLINT        DEFAULT 0,
    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chunk_id, created_at)
) PARTITION BY RANGE (created_at);

CREATE TABLE IF NOT EXISTS chunks_2026_05 PARTITION OF chunks FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS chunks_2026_06 PARTITION OF chunks FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE IF NOT EXISTS chunks_2026_07 PARTITION OF chunks FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS chunks_default PARTITION OF chunks DEFAULT;

CREATE INDEX IF NOT EXISTS idx_chunk_doc ON chunks(doc_id, chunk_index);
CREATE INDEX IF NOT EXISTS idx_chunk_tenant ON chunks(tenant_id);

-- ============================================================
-- [D] 라벨링
-- ============================================================
CREATE TABLE IF NOT EXISTS document_labels (
    doc_id          UUID            PRIMARY KEY REFERENCES documents(doc_id) ON DELETE CASCADE,
    level_id        INT             NOT NULL REFERENCES classification_levels(level_id) ON DELETE RESTRICT,
    labeled_by      VARCHAR(30)     NOT NULL,
    labeler_id      VARCHAR(50),
    confidence      DECIMAL(3,2),
    total_score     DECIMAL(4,2),
    notes           TEXT,
    is_verified     BOOLEAN         DEFAULT FALSE,
    verified_by     VARCHAR(50),
    labeled_at      TIMESTAMPTZ     DEFAULT NOW(),
    verified_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_dl_level ON document_labels(level_id);
CREATE INDEX IF NOT EXISTS idx_dl_labeled_by ON document_labels(labeled_by);

CREATE TABLE IF NOT EXISTS document_factor_scores (
    doc_id          UUID            NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    factor_id       INT             NOT NULL REFERENCES evaluation_factors(factor_id) ON DELETE RESTRICT,
    score           DECIMAL(4,2)    NOT NULL CHECK (score >= 0 AND score <= 5),
    PRIMARY KEY (doc_id, factor_id)
);

-- ============================================================
-- [E] 추론
-- ============================================================
CREATE TABLE IF NOT EXISTS classifications (
    classification_id   UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id              UUID            NOT NULL REFERENCES documents(doc_id) ON DELETE RESTRICT,
    tenant_id           VARCHAR(50)     NOT NULL REFERENCES tenants(tenant_id),
    model_version       VARCHAR(50)     NOT NULL,
    predicted_level_id  INT             NOT NULL REFERENCES classification_levels(level_id) ON DELETE RESTRICT,
    confidence          DECIMAL(5,4)    NOT NULL,
    alternatives        JSONB           NOT NULL DEFAULT '[]',
    aggregation_method  VARCHAR(20)     DEFAULT 'hybrid',
    chunk_count         SMALLINT,
    rag_used            BOOLEAN         DEFAULT FALSE,
    rag_top_k           SMALLINT,
    rag_agreement       BOOLEAN,
    status              VARCHAR(20)     DEFAULT 'staging',
    inference_ms        INT,
    classified_at       TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_cls_doc ON classifications(doc_id, classified_at DESC);
CREATE INDEX IF NOT EXISTS idx_cls_tenant_status ON classifications(tenant_id, status);
CREATE INDEX IF NOT EXISTS idx_cls_model_level ON classifications(model_version, predicted_level_id, status);
CREATE INDEX IF NOT EXISTS idx_cls_staging
  ON classifications(classified_at DESC) WHERE status = 'staging';

CREATE TABLE IF NOT EXISTS classification_evidence (
    evidence_id         BIGSERIAL       PRIMARY KEY,
    classification_id   UUID            NOT NULL REFERENCES classifications(classification_id) ON DELETE CASCADE,
    chunk_id            UUID            NOT NULL,
    evidence_type       VARCHAR(20)     NOT NULL,
    factor_id           INT             REFERENCES evaluation_factors(factor_id) ON DELETE RESTRICT,
    excerpt             TEXT            NOT NULL,
    excerpt_start       INT,
    excerpt_end         INT,
    contribution        DECIMAL(4,3)    NOT NULL,
    attention_scores    JSONB,
    rag_ref_doc_id      UUID,
    rag_similarity      DECIMAL(4,3),
    created_at          TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_ce_classification ON classification_evidence(classification_id);
CREATE INDEX IF NOT EXISTS idx_ce_chunk_contrib ON classification_evidence(chunk_id, contribution DESC);

-- ============================================================
-- [F] 학습
-- ============================================================
CREATE TABLE IF NOT EXISTS model_versions (
    version_id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    version_label       VARCHAR(50)     NOT NULL UNIQUE,
    base_model          VARCHAR(100)    NOT NULL,
    model_type          VARCHAR(20)     DEFAULT 'classifier',
    trained_at          TIMESTAMPTZ,
    training_run_id     UUID,
    training_data_count INT,
    metrics             JSONB           NOT NULL DEFAULT '{}',
    model_uri           VARCHAR(500),
    model_size_mb       INT,
    mlflow_run_id       VARCHAR(64),
    is_active           BOOLEAN         DEFAULT FALSE,
    activated_at        TIMESTAMPTZ,
    deactivated_at      TIMESTAMPTZ,
    rolled_back_from    UUID            REFERENCES model_versions(version_id),
    rollback_reason     TEXT,
    level_snapshot      JSONB,
    created_at          TIMESTAMPTZ     DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_mv_active
  ON model_versions(is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_mv_mlflow ON model_versions(mlflow_run_id);

CREATE TABLE IF NOT EXISTS training_runs (
    run_id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    model_version       UUID            REFERENCES model_versions(version_id),
    mlflow_run_id       VARCHAR(64),
    status              VARCHAR(20)     DEFAULT 'queued',
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    duration_sec        INT,
    total_samples       INT             NOT NULL,
    train_count         INT,
    val_count           INT,
    test_count          INT,
    split_method        VARCHAR(30)     DEFAULT 'stratified',
    split_seed           INT,
    hyperparameters     JSONB           NOT NULL DEFAULT '{}',
    gpu_info            JSONB,
    final_metrics       JSONB,
    trigger_type        VARCHAR(30)     DEFAULT 'manual',
    trigger_ref         VARCHAR(100),
    error_message       TEXT,
    created_at          TIMESTAMPTZ     DEFAULT NOW(),
    created_by          VARCHAR(50)
);

CREATE INDEX IF NOT EXISTS idx_tr_model ON training_runs(model_version);
CREATE INDEX IF NOT EXISTS idx_tr_status ON training_runs(status);
CREATE INDEX IF NOT EXISTS idx_tr_date ON training_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS training_epochs (
    run_id          UUID            NOT NULL REFERENCES training_runs(run_id) ON DELETE CASCADE,
    epoch           INT             NOT NULL,
    train_loss      REAL,
    val_loss        REAL,
    val_metrics     JSONB           NOT NULL DEFAULT '{}',
    learning_rate   REAL,
    logged_at       TIMESTAMPTZ     DEFAULT NOW(),
    PRIMARY KEY (run_id, epoch)
);

CREATE TABLE IF NOT EXISTS training_datasets (
    id              BIGSERIAL       PRIMARY KEY,
    run_id          UUID            NOT NULL REFERENCES training_runs(run_id) ON DELETE CASCADE,
    doc_id          UUID            NOT NULL REFERENCES documents(doc_id) ON DELETE RESTRICT,
    split_type      VARCHAR(10)     NOT NULL,
    level_id        INT             NOT NULL REFERENCES classification_levels(level_id) ON DELETE RESTRICT,
    UNIQUE(run_id, doc_id)
);

CREATE INDEX IF NOT EXISTS idx_td_run_split ON training_datasets(run_id, split_type);
CREATE INDEX IF NOT EXISTS idx_td_doc ON training_datasets(doc_id);

-- ============================================================
-- [G] 보정
-- ============================================================
CREATE TABLE IF NOT EXISTS corrections (
    correction_id       BIGSERIAL       PRIMARY KEY,
    classification_id   UUID            NOT NULL REFERENCES classifications(classification_id) ON DELETE CASCADE,
    original_level_id   INT             NOT NULL REFERENCES classification_levels(level_id) ON DELETE RESTRICT,
    corrected_level_id  INT             NOT NULL REFERENCES classification_levels(level_id) ON DELETE RESTRICT,
    direction           VARCHAR(10)     NOT NULL,
    reason              TEXT,
    corrected_by        VARCHAR(50)     NOT NULL,
    corrected_at        TIMESTAMPTZ     DEFAULT NOW(),
    consumed_in_run     UUID            REFERENCES training_runs(run_id),
    consumed_at         TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_corr_cls ON corrections(classification_id);
CREATE INDEX IF NOT EXISTS idx_corr_direction ON corrections(direction);
CREATE INDEX IF NOT EXISTS idx_corr_unconsumed
  ON corrections(consumed_in_run) WHERE consumed_in_run IS NULL;
CREATE INDEX IF NOT EXISTS idx_corr_at ON corrections(corrected_at DESC);

-- ============================================================
-- [H] 샘플 생성
-- ============================================================
CREATE TABLE IF NOT EXISTS prompt_versions (
    prompt_version      VARCHAR(30)     PRIMARY KEY,
    chain_stage         VARCHAR(20)     NOT NULL,
    template            TEXT            NOT NULL,
    avg_quality_score   DECIMAL(3,2),
    usage_count         INT             DEFAULT 0,
    approval_rate       DECIMAL(3,2),
    created_at          TIMESTAMPTZ     DEFAULT NOW(),
    created_by          VARCHAR(50),
    notes               TEXT
);

CREATE TABLE IF NOT EXISTS sample_documents (
    sample_id           UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id              UUID            REFERENCES documents(doc_id),
    tenant_id           VARCHAR(50)     NOT NULL REFERENCES tenants(tenant_id),
    target_level_id     INT             NOT NULL REFERENCES classification_levels(level_id) ON DELETE RESTRICT,
    doc_type            VARCHAR(50),
    outline_prompt_version  VARCHAR(30) REFERENCES prompt_versions(prompt_version),
    body_prompt_version     VARCHAR(30) REFERENCES prompt_versions(prompt_version),
    qc_prompt_version       VARCHAR(30) REFERENCES prompt_versions(prompt_version),
    llm_provider        VARCHAR(30)     NOT NULL,
    llm_model           VARCHAR(50)     NOT NULL,
    generated_outline   TEXT,
    generated_content   TEXT            NOT NULL,
    quality_score       DECIMAL(3,2),
    quality_report      JSONB,
    review_status       VARCHAR(20)     DEFAULT 'pending_review',
    reviewed_by         VARCHAR(50),
    reviewed_at         TIMESTAMPTZ,
    rejection_reason    TEXT,
    created_at          TIMESTAMPTZ     DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sd_status ON sample_documents(review_status);
CREATE INDEX IF NOT EXISTS idx_sd_level ON sample_documents(target_level_id);
CREATE INDEX IF NOT EXISTS idx_sd_tenant ON sample_documents(tenant_id);

-- ============================================================
-- [I] 비용 (월별 파티션)
-- ============================================================
CREATE TABLE IF NOT EXISTS llm_usage (
    usage_id            BIGSERIAL,
    provider            VARCHAR(30)     NOT NULL,
    model               VARCHAR(50)     NOT NULL,
    purpose             VARCHAR(30)     NOT NULL,
    reference_type      VARCHAR(20),
    reference_id        VARCHAR(100),
    tenant_id           VARCHAR(50)     REFERENCES tenants(tenant_id),
    input_tokens        INT             NOT NULL,
    output_tokens       INT             NOT NULL,
    total_tokens        INT             GENERATED ALWAYS AS (input_tokens + output_tokens) STORED,
    cost_usd            DECIMAL(10,6)   NOT NULL,
    cost_krw            DECIMAL(12,2),
    billing_phase       VARCHAR(20)     NOT NULL DEFAULT 'development',
    latency_ms          INT,
    success             BOOLEAN         DEFAULT TRUE,
    error_code          VARCHAR(50),
    called_at           TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (usage_id, called_at)
) PARTITION BY RANGE (called_at);

CREATE TABLE IF NOT EXISTS llm_usage_2026_05 PARTITION OF llm_usage FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS llm_usage_2026_06 PARTITION OF llm_usage FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE IF NOT EXISTS llm_usage_2026_07 PARTITION OF llm_usage FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE IF NOT EXISTS llm_usage_default PARTITION OF llm_usage DEFAULT;

CREATE INDEX IF NOT EXISTS idx_lu_phase ON llm_usage(billing_phase, called_at DESC);
CREATE INDEX IF NOT EXISTS idx_lu_purpose ON llm_usage(purpose);
CREATE INDEX IF NOT EXISTS idx_lu_ref ON llm_usage(reference_type, reference_id);

-- ============================================================
-- [J] 감사 로그 (월별 파티션)
-- ============================================================
CREATE TABLE IF NOT EXISTS audit_log (
    audit_id        BIGSERIAL,
    request_id      UUID,
    tenant_id       VARCHAR(50),
    actor_id        VARCHAR(50),
    actor_role      VARCHAR(30),
    action          VARCHAR(50)     NOT NULL,
    target_type     VARCHAR(30),
    target_id       VARCHAR(100),
    payload_hash    VARCHAR(64),
    ip_address      INET,
    user_agent      VARCHAR(500),
    success         BOOLEAN         DEFAULT TRUE,
    error_code      VARCHAR(50),
    occurred_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (audit_id, occurred_at)
) PARTITION BY RANGE (occurred_at);

CREATE TABLE IF NOT EXISTS audit_log_2026_05 PARTITION OF audit_log FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE IF NOT EXISTS audit_log_2026_06 PARTITION OF audit_log FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE IF NOT EXISTS audit_log_default PARTITION OF audit_log DEFAULT;

CREATE INDEX IF NOT EXISTS idx_audit_actor ON audit_log(actor_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit_log(target_type, target_id);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action, occurred_at DESC);

-- ============================================================
-- 무결성 트리거
-- ============================================================
CREATE OR REPLACE FUNCTION update_timestamp() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_cl_updated ON classification_levels;
CREATE TRIGGER trg_cl_updated BEFORE UPDATE ON classification_levels
  FOR EACH ROW EXECUTE FUNCTION update_timestamp();

DROP TRIGGER IF EXISTS trg_ef_updated ON evaluation_factors;
CREATE TRIGGER trg_ef_updated BEFORE UPDATE ON evaluation_factors
  FOR EACH ROW EXECUTE FUNCTION update_timestamp();

CREATE OR REPLACE FUNCTION check_factor_weights_sum() RETURNS TRIGGER AS $$
DECLARE
  total DECIMAL(4,3);
BEGIN
  SELECT COALESCE(SUM(weight), 0) INTO total
  FROM evaluation_factors WHERE is_active = TRUE;
  IF ABS(total - 1.0) > 0.01 THEN
    RAISE EXCEPTION 'evaluation_factors active weight sum must be 1.0 (current: %)', total;
  END IF;
  RETURN NULL;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_factor_weights_sum ON evaluation_factors;
CREATE CONSTRAINT TRIGGER trg_factor_weights_sum
  AFTER INSERT OR UPDATE OR DELETE ON evaluation_factors
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION check_factor_weights_sum();

-- ============================================================
-- 운영 뷰
-- ============================================================
CREATE OR REPLACE VIEW v_classification_final AS
SELECT
  c.classification_id,
  c.doc_id,
  c.tenant_id,
  c.model_version,
  c.predicted_level_id,
  pl.level_code AS predicted_code,
  COALESCE(latest_corr.corrected_level_id, c.predicted_level_id) AS final_level_id,
  COALESCE(fl.level_code, pl.level_code) AS final_code,
  c.confidence,
  c.status,
  c.classified_at,
  latest_corr.corrected_at
FROM classifications c
JOIN classification_levels pl ON c.predicted_level_id = pl.level_id
LEFT JOIN LATERAL (
  SELECT corrected_level_id, corrected_at
  FROM corrections
  WHERE classification_id = c.classification_id AND direction != 'confirm'
  ORDER BY corrected_at DESC LIMIT 1
) latest_corr ON true
LEFT JOIN classification_levels fl ON latest_corr.corrected_level_id = fl.level_id;

CREATE OR REPLACE VIEW v_monthly_llm_cost AS
SELECT date_trunc('month', called_at) AS month,
       billing_phase, provider, model, purpose,
       COUNT(*) AS call_count,
       SUM(input_tokens) AS total_input_tokens,
       SUM(output_tokens) AS total_output_tokens,
       SUM(cost_usd) AS total_cost_usd,
       SUM(cost_krw) AS total_cost_krw,
       AVG(latency_ms)::INT AS avg_latency_ms,
       COUNT(*) FILTER (WHERE NOT success) AS error_count
FROM llm_usage
GROUP BY 1, 2, 3, 4, 5;

CREATE OR REPLACE VIEW v_model_performance AS
SELECT c.model_version,
       cl.level_code, cl.level_name,
       COUNT(*) AS total_classified,
       COUNT(*) FILTER (WHERE c.status = 'confirmed') AS confirmed_count,
       COUNT(*) FILTER (WHERE c.status = 'corrected') AS corrected_count,
       COUNT(*) FILTER (WHERE cr.direction = 'underclass') AS underclass_count,
       COUNT(*) FILTER (WHERE cr.direction = 'overclass') AS overclass_count,
       AVG(c.confidence)::DECIMAL(5,4) AS avg_confidence,
       AVG(c.inference_ms)::INT AS avg_inference_ms
FROM classifications c
JOIN classification_levels cl ON c.predicted_level_id = cl.level_id
LEFT JOIN corrections cr ON c.classification_id = cr.classification_id
GROUP BY c.model_version, cl.level_code, cl.level_name;

CREATE OR REPLACE VIEW v_active_learning_status AS
SELECT
  COUNT(*) FILTER (WHERE consumed_in_run IS NULL) AS unconsumed_corrections,
  COUNT(*) FILTER (WHERE direction = 'underclass' AND consumed_in_run IS NULL) AS pending_underclass,
  MAX(corrected_at) AS last_correction_at,
  CASE
    WHEN COUNT(*) FILTER (WHERE direction = 'underclass'
                          AND consumed_in_run IS NULL) >= 10 THEN 'URGENT_RETRAIN'
    WHEN COUNT(*) FILTER (WHERE consumed_in_run IS NULL) >= 50 THEN 'RETRAIN_RECOMMENDED'
    ELSE 'OK'
  END AS retrain_status
FROM corrections;
"""


def upgrade() -> None:
    """Apply init.sql v2 schema (idempotent — IF NOT EXISTS 전역 적용)."""
    op.execute(_DDL)


def downgrade() -> None:
    """No-op — baseline은 downgrade 불가 (production DB 보호).

    수동으로 전체 DROP이 필요한 경우 별도 teardown 스크립트 사용 권장.
    """
