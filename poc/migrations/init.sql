CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS grade_schema (
  schema_version   TEXT PRIMARY KEY,
  grades           JSONB NOT NULL,
  effective_from   TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_by       TEXT
);

INSERT INTO grade_schema (schema_version, grades, created_by)
VALUES (
  'v1',
  '[
    {"code":"TS","name":"특급기밀","order":4,"color":"#7B1FA2"},
    {"code":"S1","name":"1급 비밀","order":3,"color":"#D32F2F"},
    {"code":"S2","name":"2급 대외비","order":2,"color":"#F57C00"},
    {"code":"S3","name":"3급 공개","order":1,"color":"#388E3C"}
  ]'::jsonb,
  'system'
) ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS guide_document (
  guide_id        TEXT,
  version         TEXT,
  effective_date  DATE,
  change_summary  TEXT,
  storage_uri     TEXT,
  indexed_at      TIMESTAMPTZ,
  PRIMARY KEY (guide_id, version)
);

CREATE TABLE IF NOT EXISTS preprocessed_doc (
  doc_hash        TEXT PRIMARY KEY,
  doc_id          TEXT,
  tenant_id       TEXT,
  source_format   TEXT,
  cleaned_text    TEXT,
  structure_json  JSONB,
  chunk_count     INTEGER,
  processed_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS inference_result (
  inference_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  doc_id              TEXT NOT NULL,
  tenant_id           TEXT,
  model_version       TEXT,
  predicted_grade     TEXT,
  confidence          REAL,
  scores              JSONB,
  evaluation_factors  JSONB,
  evidence            JSONB,
  rag_used            BOOLEAN DEFAULT false,
  rag_context         JSONB,
  status              TEXT NOT NULL DEFAULT 'staging',
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  confirmed_at        TIMESTAMPTZ,
  confirmed_by        TEXT
);
CREATE INDEX IF NOT EXISTS idx_inference_doc ON inference_result (doc_id, created_at DESC);

CREATE TABLE IF NOT EXISTS relabel_event (
  relabel_id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  doc_id                TEXT NOT NULL,
  inference_id          UUID,
  original_grade        TEXT,
  corrected_grade       TEXT,
  reason                TEXT,
  actor                 JSONB,
  created_at            TIMESTAMPTZ NOT NULL DEFAULT now(),
  consumed_in_training  UUID
);

CREATE TABLE IF NOT EXISTS synthetic_doc (
  synth_id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  target_grade       TEXT NOT NULL,
  domain             TEXT,
  llm_provider       TEXT,
  llm_model          TEXT,
  content            TEXT,
  prompt_hash        TEXT,
  status             TEXT NOT NULL DEFAULT 'pending',
  reviewer           TEXT,
  reviewer_decision  TEXT,
  added_to_dataset   TEXT,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS train_job (
  train_job_id     UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  training_type    TEXT,
  base_model       TEXT,
  dataset_version  TEXT,
  hyperparams      JSONB,
  status           TEXT NOT NULL DEFAULT 'queued',
  metrics          JSONB,
  model_version    TEXT,
  mlflow_run_id    TEXT,
  started_at       TIMESTAMPTZ,
  finished_at      TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS labeling_rule (
  rule_version    TEXT PRIMARY KEY,
  rules_yaml      TEXT,
  factor_weights  JSONB,
  effective_from  TIMESTAMPTZ NOT NULL DEFAULT now()
);
