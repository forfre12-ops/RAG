-- ============================================================
-- 한국지식재산보호원 AI 영업비밀 등급분류 시스템
-- Lloydk AI 파이프라인 DB 스키마 v2 (PostgreSQL 15+)
--
-- v1 → v2 주요 개선:
--   [F1] 대용량 텍스트 분리: documents.raw_text → MinIO 외부화
--   [F3] 청크 파티셔닝: chunks RANGE PARTITION (created_at)
--   [F4] (제거됨) pgvector — 자체 결정으로 ES 단일 벡터스토어 확정. 벡터 인덱스는 ES dense_vector(int8_hnsw) 단독 사용
--   [F5] FK 보호: classification_levels ON DELETE RESTRICT
--   [F6] 평가요소 정규화 테이블 추가 (document_factor_scores)
--   [F7] (B안) weight 합계 트리거 비활성 — A안 100점 보조 모드 전용
--   [F8] 보정/확정 진실 소스 단일화 (corrections만)
--   [F9] training_epochs 분리 (training_log JSONB 제거)
--   [F10] audit_log 추가 (영업비밀 시스템 필수)
--   [F11] llm_usage 월 파티셔닝
--   [F12] sample_documents ↔ llm_usage FK 연결
--   [F13] 인덱스 보강
--   [F14] OpenAPI Grade enum과 코드값 정합 (TS/S1/S2/S3)
-- ============================================================

-- pgvector 확장은 사용하지 않습니다 (벡터스토어 = ES 단일, doc/13 §4.1).
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";


-- ============================================================
-- [B] 등급 체계 도메인
-- ============================================================

CREATE TABLE classification_levels (
    level_id        SERIAL          PRIMARY KEY,
    level_code      VARCHAR(20)     NOT NULL UNIQUE,    -- TS/S1/S2/S3 (OpenAPI Grade enum 정합)
    level_name      VARCHAR(50)     NOT NULL,
    level_order     SMALLINT        NOT NULL,           -- 1=최고등급
    description     TEXT,
    color_hex       VARCHAR(7)      DEFAULT '#808080',
    loss_weight     DECIMAL(4,2)    DEFAULT 1.0,        -- BERT CE loss weight
    is_active       BOOLEAN         DEFAULT TRUE,       -- 등급 삭제 대신 비활성화
    created_at      TIMESTAMPTZ     DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     DEFAULT NOW(),
    created_by      VARCHAR(50)
);

COMMENT ON TABLE classification_levels IS '영업비밀 보안등급 정의. 관리자가 동적 추가/변경, 삭제는 비활성화로 처리 (FUN-005)';
COMMENT ON COLUMN classification_levels.level_code IS 'OpenAPI Grade enum과 1:1: TS=특급, S1=1급, S2=2급, S3=3급';
COMMENT ON COLUMN classification_levels.level_order IS '낮을수록 높은 등급. FNR 계산에 사용 (정답 order < 예측 order ⇒ underclass)';

CREATE INDEX idx_cl_active_order ON classification_levels(is_active, level_order);

-- 기본 4등급 seed (OpenAPI Grade enum 정합)
INSERT INTO classification_levels (level_code, level_name, level_order, color_hex, loss_weight) VALUES
  ('TS', '특급기밀',   1, '#7B1FA2', 3.0),
  ('S1', '1급 비밀',   2, '#D32F2F', 2.0),
  ('S2', '2급 대외비', 3, '#F57C00', 1.0),
  ('S3', '3급 공개',   4, '#388E3C', 1.0)
ON CONFLICT DO NOTHING;


CREATE TABLE evaluation_factors (
    factor_id       SERIAL          PRIMARY KEY,
    factor_code     VARCHAR(30)     NOT NULL UNIQUE,
    factor_name     VARCHAR(100)    NOT NULL,
    description     TEXT,
    weight          DECIMAL(3,2)    NOT NULL DEFAULT 0.25,
    is_active       BOOLEAN         DEFAULT TRUE,
    created_at      TIMESTAMPTZ     DEFAULT NOW(),
    updated_at      TIMESTAMPTZ     DEFAULT NOW()
);

COMMENT ON TABLE evaluation_factors IS '등급 판단 정본 3요건 S·V·M — 등급=S×V×M 곱셈 (FUN-023, 정본 가이드 B안)';

-- 정본 3요건 (B안 곱셈식). weight는 B안에서 미사용(A안 100점 보조 모드에서만).
INSERT INTO evaluation_factors (factor_code, factor_name, weight) VALUES
  ('SECRECY',    '비공지성(S)',      1.00),   -- 0이면 곱=0 → 공개 게이트
  ('VALUE',      '경제적 유용성(V)', 1.00),
  ('MANAGEMENT', '비밀관리성(M)',    1.00)
ON CONFLICT DO NOTHING;
-- 유출영향도는 별도 요소 아님 → classification_levels.loss_weight + 등급별 FNR로 관리(R4).


CREATE TABLE level_keywords (
    keyword_id      SERIAL          PRIMARY KEY,
    level_id        INT             NOT NULL REFERENCES classification_levels(level_id) ON DELETE RESTRICT,
    keyword         VARCHAR(200)    NOT NULL,
    pattern_type    VARCHAR(20)     NOT NULL DEFAULT 'exact',  -- exact/regex/semantic
    factor_id       INT             REFERENCES evaluation_factors(factor_id) ON DELETE RESTRICT,
    weight          DECIMAL(3,2)    DEFAULT 1.0,
    source          VARCHAR(30)     DEFAULT 'manual',
    example_context TEXT,
    is_active       BOOLEAN         DEFAULT TRUE,
    created_at      TIMESTAMPTZ     DEFAULT NOW()
);

COMMENT ON TABLE level_keywords IS '등급별 판별 키워드/패턴 사전 (FUN-023, M3 LabelingPipeline 외부화)';

CREATE INDEX idx_lk_level_active ON level_keywords(level_id, is_active);
CREATE INDEX idx_lk_keyword_trgm ON level_keywords USING gin (keyword gin_trgm_ops);


-- ============================================================
-- [C] 문서 저장 도메인 (대용량 텍스트는 MinIO 외부화)
-- ============================================================

CREATE TABLE documents (
    doc_id              UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    external_ref        VARCHAR(100),   -- KL FUN-007 문서 ID
    filename            VARCHAR(500)    NOT NULL,
    source_format       VARCHAR(10)     NOT NULL,
    file_size_bytes     BIGINT,
    file_hash           VARCHAR(64),    -- SHA-256

    metadata            JSONB           DEFAULT '{}',  -- {author_dept, doc_type, title, created_date, ...}

    -- 대용량 텍스트는 MinIO 외부화. PG에는 위치 + 미리보기만.
    raw_text_uri        VARCHAR(500),   -- s3://lloydk/docs/raw/{doc_id}.txt
    normalized_text_uri VARCHAR(500),   -- s3://lloydk/docs/normalized/{doc_id}.txt
    text_preview        VARCHAR(2000),  -- 검색/UI 표시용 앞부분
    char_count          INT,

    extraction_method   VARCHAR(30)     DEFAULT 'parser',  -- parser/rhwp/ocr/ocr_llm/libreoffice
    extraction_quality  DECIMAL(3,2),
    ocr_used            BOOLEAN         DEFAULT FALSE,

    processing_status   VARCHAR(20)     DEFAULT 'pending', -- pending/processing/done/failed
    error_message       TEXT,

    uploaded_at         TIMESTAMPTZ     DEFAULT NOW(),
    processed_at        TIMESTAMPTZ,
    created_by          VARCHAR(50)
);

COMMENT ON TABLE documents IS '원본 문서 메타. 대용량 텍스트는 MinIO. PG는 메타·미리보기·해시만 (FUN-022)';
COMMENT ON COLUMN documents.text_preview IS '최대 2KB 미리보기. 풀텍스트는 raw_text_uri/normalized_text_uri 참조';
COMMENT ON COLUMN documents.extraction_method IS 'parser=PyMuPDF/python-docx, rhwp=rhwp-python(1순위 HWP), ocr=PaddleOCR, ocr_llm=Qwen2-VL';

CREATE UNIQUE INDEX idx_doc_hash ON documents(file_hash) WHERE file_hash IS NOT NULL;
CREATE INDEX idx_doc_status ON documents(processing_status);
CREATE INDEX idx_doc_format ON documents(source_format);
CREATE INDEX idx_doc_metadata ON documents USING gin (metadata jsonb_path_ops);
CREATE INDEX idx_doc_uploaded ON documents(uploaded_at DESC);
CREATE INDEX idx_doc_pending ON documents(uploaded_at) WHERE processing_status = 'pending'; -- 처리 대기 큐 조회


-- 청크: 월별 파티셔닝 (대규모 운영 대비)
CREATE TABLE chunks (
    chunk_id        UUID            DEFAULT gen_random_uuid(),
    doc_id          UUID            NOT NULL,
    chunk_index     INT             NOT NULL,           -- v1 SMALLINT → INT (긴 문서 대응)
    content         TEXT            NOT NULL,
    token_count     INT             NOT NULL,
    char_count      INT             NOT NULL,

    page_start      SMALLINT,
    page_end        SMALLINT,
    section_path    TEXT[],

    -- 임베딩 벡터는 ES `secrets-guides-koipa-*` 인덱스에 저장 (chunk_id로 연결)

    overlap_prev    SMALLINT        DEFAULT 0,
    overlap_next    SMALLINT        DEFAULT 0,

    created_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (chunk_id, created_at)
) PARTITION BY RANGE (created_at);

COMMENT ON TABLE chunks IS '문서 분할 청크. 월별 파티션. 임베딩 벡터는 ES 인덱스 외부화 (FUN-004 Chunk 단위 학습)';
COMMENT ON COLUMN chunks.section_path IS '한국 공문 구조 보존: [''1장'', ''1.2절'', ''가.'']';

-- 초기 파티션 (운영 시 자동 생성 cron 권장)
CREATE TABLE chunks_2026_05 PARTITION OF chunks FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE chunks_2026_06 PARTITION OF chunks FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE chunks_2026_07 PARTITION OF chunks FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE chunks_default PARTITION OF chunks DEFAULT;

CREATE INDEX idx_chunk_doc ON chunks(doc_id, chunk_index);
-- 벡터 HNSW 인덱스는 ES 측에서 관리 (doc/13 §4.2).


-- ============================================================
-- [D] 라벨링 도메인 (정규화)
-- ============================================================

CREATE TABLE document_labels (
    doc_id          UUID            PRIMARY KEY REFERENCES documents(doc_id) ON DELETE CASCADE,
    level_id        INT             NOT NULL REFERENCES classification_levels(level_id) ON DELETE RESTRICT,
    labeled_by      VARCHAR(30)     NOT NULL,    -- expert/llm_generated/admin_corrected
    labeler_id      VARCHAR(50),
    confidence      DECIMAL(3,2),
    total_score     DECIMAL(4,2),
    notes           TEXT,
    is_verified     BOOLEAN         DEFAULT FALSE,
    verified_by     VARCHAR(50),
    labeled_at      TIMESTAMPTZ     DEFAULT NOW(),
    verified_at     TIMESTAMPTZ
);

COMMENT ON TABLE document_labels IS '문서 최종 확정 라벨. 학습 데이터셋 기준 (FUN-023)';

CREATE INDEX idx_dl_level ON document_labels(level_id);
CREATE INDEX idx_dl_labeled_by ON document_labels(labeled_by);


-- 평가요소 점수 정규화 (v1 factor_scores JSONB → 정규화)
CREATE TABLE document_factor_scores (
    doc_id          UUID            NOT NULL REFERENCES documents(doc_id) ON DELETE CASCADE,
    factor_id       INT             NOT NULL REFERENCES evaluation_factors(factor_id) ON DELETE RESTRICT,
    score           SMALLINT        NOT NULL CHECK (score >= 0 AND score <= 2),  -- S/V/M 각 0·1·2 (B안)
    PRIMARY KEY (doc_id, factor_id)
);

COMMENT ON TABLE document_factor_scores IS '정본 3요건 S·V·M 점수(각 0~2) 정규화 저장. FK 무결성 보장';


-- ============================================================
-- [E] AI 추론 도메인
-- ============================================================

CREATE TABLE classifications (
    classification_id   UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id              UUID            NOT NULL REFERENCES documents(doc_id) ON DELETE RESTRICT,
    model_version       VARCHAR(50)     NOT NULL,

    predicted_level_id  INT             NOT NULL REFERENCES classification_levels(level_id) ON DELETE RESTRICT,
    confidence          DECIMAL(5,4)    NOT NULL,
    alternatives        JSONB           NOT NULL DEFAULT '[]',
    -- alternatives 스키마: [{"level_id": 2, "level_code": "S1", "confidence": 0.13}, ...]

    aggregation_method  VARCHAR(20)     DEFAULT 'hybrid',  -- mean/max/voting/hybrid
    chunk_count         SMALLINT,

    rag_used            BOOLEAN         DEFAULT FALSE,
    rag_top_k           SMALLINT,
    rag_agreement       BOOLEAN,

    status              VARCHAR(20)     DEFAULT 'staging',
    -- staging → confirmed/corrected/rejected (확정 라벨은 corrections.corrected_level_id가 진실 소스)

    inference_ms        INT,
    classified_at       TIMESTAMPTZ     DEFAULT NOW()
);

COMMENT ON TABLE classifications IS 'AI 분류 결과. 시계열 누적 (FUN-005). 확정 라벨은 corrections 테이블 진실 소스';
COMMENT ON COLUMN classifications.aggregation_method IS 'hybrid: 고등급 청크가 임계치 초과 시 자동 격상 (FNR 최소화 전략)';
COMMENT ON COLUMN classifications.alternatives IS 'JSONB 스키마: [{"level_id":int, "level_code":str, "confidence":float}, ...]';

CREATE INDEX idx_cls_doc ON classifications(doc_id, classified_at DESC);
CREATE INDEX idx_cls_status ON classifications(status);
CREATE INDEX idx_cls_classified ON classifications(classified_at);
CREATE INDEX idx_cls_model_level ON classifications(model_version, predicted_level_id, status);
CREATE INDEX idx_cls_staging ON classifications(classified_at DESC) WHERE status = 'staging';


CREATE TABLE classification_evidence (
    evidence_id         BIGSERIAL       PRIMARY KEY,
    classification_id   UUID            NOT NULL REFERENCES classifications(classification_id) ON DELETE CASCADE,
    chunk_id            UUID            NOT NULL,
    -- 주: chunks가 파티션 테이블이라 FK 미설정. 무결성은 애플리케이션 레벨에서 보장.

    evidence_type       VARCHAR(20)     NOT NULL,  -- attention/keyword_match/rag_reference/pattern_match
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

COMMENT ON TABLE classification_evidence IS '분류 판정 근거. 각 evidence의 기여도 (합 ≈ 1.0) (FUN-005, FUN-024)';

CREATE INDEX idx_ce_classification ON classification_evidence(classification_id);
CREATE INDEX idx_ce_chunk_contrib ON classification_evidence(chunk_id, contribution DESC);


-- ============================================================
-- [F] 학습 도메인
-- ============================================================

CREATE TABLE model_versions (
    version_id          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    version_label       VARCHAR(50)     NOT NULL UNIQUE,  -- 사람이 읽는 라벨 v1.3.0 등
    base_model          VARCHAR(100)    NOT NULL,         -- kakaobank/kf-deberta-base
    model_type          VARCHAR(20)     DEFAULT 'classifier',

    trained_at          TIMESTAMPTZ,
    training_run_id     UUID,                             -- training_runs(run_id) ref (FK는 순환방지 위해 미설정)
    training_data_count INT,

    metrics             JSONB           NOT NULL DEFAULT '{}',
    -- 스키마: {accuracy, f1_macro, fnr_overall, per_class:{TS:{precision,recall,f1,fnr},...}, confusion_matrix}

    model_uri           VARCHAR(500),                     -- s3://lloydk/models/{version_id}/
    model_size_mb       INT,

    mlflow_run_id       VARCHAR(64),                      -- v1에 누락된 MLflow 추적 키

    is_active           BOOLEAN         DEFAULT FALSE,
    activated_at        TIMESTAMPTZ,
    deactivated_at      TIMESTAMPTZ,

    rolled_back_from    UUID            REFERENCES model_versions(version_id),
    rollback_reason     TEXT,
    level_snapshot      JSONB,                            -- 학습 시점 등급체계 보존

    created_at          TIMESTAMPTZ     DEFAULT NOW()
);

COMMENT ON TABLE model_versions IS '모델 버전 레지스트리. MLflow 연동 (FUN-004, FUN-024)';
COMMENT ON COLUMN model_versions.version_label IS '사람이 읽는 라벨. 실제 PK는 UUID라 충돌·재사용 안전';
COMMENT ON COLUMN model_versions.level_snapshot IS '학습 시점 등급체계 보존. 등급 변경 후 과거 모델 해석용';

CREATE UNIQUE INDEX idx_mv_active ON model_versions(is_active) WHERE is_active = TRUE;
CREATE INDEX idx_mv_mlflow ON model_versions(mlflow_run_id);


CREATE TABLE training_runs (
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
    split_seed          INT,

    hyperparameters     JSONB           NOT NULL DEFAULT '{}',
    gpu_info            JSONB,
    final_metrics       JSONB,

    trigger_type        VARCHAR(30)     DEFAULT 'manual',  -- manual/active_learning/schedule/level_change
    trigger_ref         VARCHAR(100),
    error_message       TEXT,

    created_at          TIMESTAMPTZ     DEFAULT NOW(),
    created_by          VARCHAR(50)
);

COMMENT ON TABLE training_runs IS '학습 실행 이력. epoch 로그는 training_epochs 분리 (FUN-004)';

CREATE INDEX idx_tr_model ON training_runs(model_version);
CREATE INDEX idx_tr_status ON training_runs(status);
CREATE INDEX idx_tr_date ON training_runs(started_at DESC);


-- v1의 training_log JSONB '[]' → 별도 테이블로 분리 (단일 row 비대화 방지)
CREATE TABLE training_epochs (
    run_id          UUID            NOT NULL REFERENCES training_runs(run_id) ON DELETE CASCADE,
    epoch           INT             NOT NULL,
    train_loss      REAL,
    val_loss        REAL,
    val_metrics     JSONB           NOT NULL DEFAULT '{}',  -- {accuracy, f1_macro, fnr_overall, ...}
    learning_rate   REAL,
    logged_at       TIMESTAMPTZ     DEFAULT NOW(),
    PRIMARY KEY (run_id, epoch)
);

COMMENT ON TABLE training_epochs IS 'epoch별 학습 로그. training_runs와 분리하여 row 비대화 방지';


CREATE TABLE training_datasets (
    id              BIGSERIAL       PRIMARY KEY,
    run_id          UUID            NOT NULL REFERENCES training_runs(run_id) ON DELETE CASCADE,
    doc_id          UUID            NOT NULL REFERENCES documents(doc_id) ON DELETE RESTRICT,
    split_type      VARCHAR(10)     NOT NULL,  -- train/val/test
    level_id        INT             NOT NULL REFERENCES classification_levels(level_id) ON DELETE RESTRICT,
    UNIQUE(run_id, doc_id)
);

CREATE INDEX idx_td_run_split ON training_datasets(run_id, split_type);
CREATE INDEX idx_td_doc ON training_datasets(doc_id);


-- ============================================================
-- [G] 보정 도메인 — 확정 라벨의 진실 소스
-- ============================================================

CREATE TABLE corrections (
    correction_id       BIGSERIAL       PRIMARY KEY,
    classification_id   UUID            NOT NULL REFERENCES classifications(classification_id) ON DELETE CASCADE,

    -- corrections 자체가 진실 소스. classifications에는 status만.
    -- 보정 없으면 → predicted_level_id가 확정 (별도 INSERT 불필요)
    -- 보정 있으면 → 가장 최신 corrected_level_id가 확정

    original_level_id   INT             NOT NULL REFERENCES classification_levels(level_id) ON DELETE RESTRICT,
    corrected_level_id  INT             NOT NULL REFERENCES classification_levels(level_id) ON DELETE RESTRICT,

    direction           VARCHAR(10)     NOT NULL,  -- underclass/overclass/lateral/confirm
    reason              TEXT,
    corrected_by        VARCHAR(50)     NOT NULL,
    corrected_at        TIMESTAMPTZ     DEFAULT NOW(),

    consumed_in_run     UUID            REFERENCES training_runs(run_id),
    consumed_at         TIMESTAMPTZ
);

COMMENT ON TABLE corrections IS 'Active Learning 입력. direction=underclass는 보안 미탐 = 핵심 위험 (FUN-024)';
COMMENT ON COLUMN corrections.direction IS 'underclass=고→저(미탐, 위험), overclass=저→고(과탐, 불편), lateral=동급, confirm=확정만(보정X)';

CREATE INDEX idx_corr_cls ON corrections(classification_id);
CREATE INDEX idx_corr_direction ON corrections(direction);
CREATE INDEX idx_corr_unconsumed ON corrections(consumed_in_run) WHERE consumed_in_run IS NULL;
CREATE INDEX idx_corr_at ON corrections(corrected_at DESC);


-- ============================================================
-- [H] 샘플 생성 도메인
-- ============================================================

CREATE TABLE prompt_versions (
    prompt_version      VARCHAR(30)     PRIMARY KEY,
    chain_stage         VARCHAR(20)     NOT NULL,   -- outline/body/quality_check
    template            TEXT            NOT NULL,
    avg_quality_score   DECIMAL(3,2),
    usage_count         INT             DEFAULT 0,
    approval_rate       DECIMAL(3,2),
    created_at          TIMESTAMPTZ     DEFAULT NOW(),
    created_by          VARCHAR(50),
    notes               TEXT
);

COMMENT ON TABLE prompt_versions IS '샘플 생성 프롬프트 체인 버전. ROI 측정 (FUN-003)';


CREATE TABLE sample_documents (
    sample_id           UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    doc_id              UUID            REFERENCES documents(doc_id),  -- 승인 후 편입 시

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

COMMENT ON TABLE sample_documents IS 'LLM 생성 합성 문서. 승인 시 documents 편입 (FUN-003)';
COMMENT ON COLUMN sample_documents.doc_id IS '검수 승인 시 documents에 INSERT 후 FK 연결. 비용은 llm_usage.reference_id로 추적';

CREATE INDEX idx_sd_status ON sample_documents(review_status);
CREATE INDEX idx_sd_level ON sample_documents(target_level_id);


-- ============================================================
-- [I] 비용 추적 도메인 (월별 파티셔닝)
-- ============================================================

CREATE TABLE llm_usage (
    usage_id            BIGSERIAL,
    provider            VARCHAR(30)     NOT NULL,    -- anthropic/openai/google/vllm
    model               VARCHAR(50)     NOT NULL,

    purpose             VARCHAR(30)     NOT NULL,    -- sample_generation/ocr_llm/rag_query/keyword_extraction
    reference_type      VARCHAR(20),                 -- sample/classification/extraction
    reference_id        VARCHAR(100),                -- 해당 도메인의 PK (sample_id 등)

    input_tokens        INT             NOT NULL,
    output_tokens       INT             NOT NULL,
    total_tokens        INT             GENERATED ALWAYS AS (input_tokens + output_tokens) STORED,

    cost_usd            DECIMAL(10,6)   NOT NULL,
    cost_krw            DECIMAL(12,2),

    billing_phase       VARCHAR(20)     NOT NULL DEFAULT 'development',  -- development/operation

    latency_ms          INT,
    success             BOOLEAN         DEFAULT TRUE,
    error_code          VARCHAR(50),

    called_at           TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (usage_id, called_at)
) PARTITION BY RANGE (called_at);

COMMENT ON TABLE llm_usage IS 'LLM 호출 비용 추적. 월별 파티션. FUN-003 개발/운영 비용 분리';
COMMENT ON COLUMN llm_usage.billing_phase IS 'development: 수행사 부담, operation: 발주처 부담';

CREATE TABLE llm_usage_2026_05 PARTITION OF llm_usage FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE llm_usage_2026_06 PARTITION OF llm_usage FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE llm_usage_2026_07 PARTITION OF llm_usage FOR VALUES FROM ('2026-07-01') TO ('2026-08-01');
CREATE TABLE llm_usage_default PARTITION OF llm_usage DEFAULT;

CREATE INDEX idx_lu_phase ON llm_usage(billing_phase, called_at DESC);
CREATE INDEX idx_lu_purpose ON llm_usage(purpose);
CREATE INDEX idx_lu_ref ON llm_usage(reference_type, reference_id);


-- ============================================================
-- [J] 감사 로그 — 영업비밀 시스템 필수
-- ============================================================

CREATE TABLE audit_log (
    audit_id        BIGSERIAL,
    request_id      UUID,
    actor_id        VARCHAR(50),
    actor_role      VARCHAR(30),       -- admin/reviewer/system/kl_backend
    action          VARCHAR(50)     NOT NULL,  -- classify/confirm/relabel/train/synth/...
    target_type     VARCHAR(30),       -- document/classification/model/...
    target_id       VARCHAR(100),
    payload_hash    VARCHAR(64),       -- 요청 본문 SHA-256 (재현 가능, 내용 비저장)
    ip_address      INET,
    user_agent      VARCHAR(500),
    success         BOOLEAN         DEFAULT TRUE,
    error_code      VARCHAR(50),
    occurred_at     TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    PRIMARY KEY (audit_id, occurred_at)
) PARTITION BY RANGE (occurred_at);

COMMENT ON TABLE audit_log IS '모든 API 호출 감사 로그. 영업비밀 시스템 보안 필수 (04 §9.5)';
COMMENT ON COLUMN audit_log.payload_hash IS '본문 SHA-256만 저장. 원문은 보안상 PG에 미저장';

CREATE TABLE audit_log_2026_05 PARTITION OF audit_log FOR VALUES FROM ('2026-05-01') TO ('2026-06-01');
CREATE TABLE audit_log_2026_06 PARTITION OF audit_log FOR VALUES FROM ('2026-06-01') TO ('2026-07-01');
CREATE TABLE audit_log_default PARTITION OF audit_log DEFAULT;

CREATE INDEX idx_audit_actor ON audit_log(actor_id, occurred_at DESC);
CREATE INDEX idx_audit_target ON audit_log(target_type, target_id);
CREATE INDEX idx_audit_action ON audit_log(action, occurred_at DESC);


-- ============================================================
-- 무결성 트리거
-- ============================================================

-- updated_at 자동 갱신
CREATE OR REPLACE FUNCTION update_timestamp() RETURNS TRIGGER AS $$
BEGIN NEW.updated_at = NOW(); RETURN NEW; END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_cl_updated BEFORE UPDATE ON classification_levels
  FOR EACH ROW EXECUTE FUNCTION update_timestamp();
CREATE TRIGGER trg_ef_updated BEFORE UPDATE ON evaluation_factors
  FOR EACH ROW EXECUTE FUNCTION update_timestamp();


-- (v2/B안) 정본 가이드는 곱셈식 등급 = S×V×M(각 0~2)이라 가산 가중치를 쓰지 않는다.
-- 따라서 weight 합계=1.0 보장 트리거는 비활성화한다(3요소이므로 합계 1.0도 성립 안 함).
-- weight 컬럼·sum 트리거는 A안(5요소 100점 가산식) 보조 모드를 켤 때만 사용 →
-- A안 보조 활성화 시에만 check_factor_weights_sum 트리거를 재생성할 것(git 이력에 원본 보존).


-- ============================================================
-- 운영 뷰
-- ============================================================

-- 확정 라벨 단일 진실 소스 뷰 (corrections 기준)
CREATE OR REPLACE VIEW v_classification_final AS
SELECT
  c.classification_id,
  c.doc_id,
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

COMMENT ON VIEW v_classification_final IS '확정 라벨 단일 진실 소스. corrections 최신 + 없으면 predicted';


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

-- ============================================================
-- END OF SCHEMA v2
-- ============================================================
