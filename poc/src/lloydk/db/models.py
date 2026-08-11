"""ORM 매핑 (init.sql v2 기반).

도메인 그룹:
  B 등급체계:     ClassificationLevel, EvaluationFactor, LevelKeyword
  C 문서:         Document, Chunk
  D 라벨링:       DocumentLabel, DocumentFactorScore
  E 추론:         Classification, ClassificationEvidence
  F 학습:         ModelVersion, TrainingRun, TrainingEpoch, TrainingDataset
  G 보정:         Correction
  H 샘플 생성:    PromptVersion, SampleDocument
  I 비용:         LlmUsage  (월별 파티션 부모)
  J 감사:         AuditLog  (월별 파티션 부모)

설계 노트:
- chunks·llm_usage·audit_log는 RANGE PARTITION 테이블. 부모만 매핑.
- 보정/성능 집계 운영 뷰 3개(v_classification_final·v_model_performance·
  v_active_learning_status)는 migration f1e2d3c4b5a6에서 DROP — 동일 로직이
  metrics.py/active_learning.py/confirm_service.py에 코드로 단일화됨. 비용 집계
  뷰 v_monthly_llm_cost만 잔존(raw SQL로 조회).
- weight 합계 검증 트리거는 DB 측 보장 — ORM은 검증 안 함.
- INET 타입은 sqlalchemy.dialects.postgresql.INET 사용.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    REAL,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from lloydk.db.session import Base

# tenant 제거: 격리는 KL 포털 전담 (2026-06-24 멀티테넌트 전면 제거 결정).
# Lloydk는 단일 고객사 엔진 — per-customer 경계는 상류(KL 포털 라우팅)가 보장.


# ============================================================
# [B] 등급체계
# ============================================================

class ClassificationLevel(Base):
    __tablename__ = "tb_classification_levels"

    level_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    level_name: Mapped[str] = mapped_column(String(50), nullable=False)
    level_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color_hex: Mapped[str | None] = mapped_column(String(7), default="#808080", server_default=text("'#808080'::character varying"))
    loss_weight: Mapped[float | None] = mapped_column(Numeric(4, 2), default=1.0, server_default=text("1.0"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(50))

    __table_args__ = (
        Index("idx_cl_active_order", "is_active", "level_order"),
    )


class EvaluationFactor(Base):
    __tablename__ = "tb_evaluation_factors"

    factor_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    factor_code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    factor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    weight: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False, default=0.25, server_default=text("0.25"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LevelKeyword(Base):
    __tablename__ = "tb_level_keywords"

    keyword_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level_id: Mapped[int] = mapped_column(ForeignKey("tb_classification_levels.level_id", ondelete="RESTRICT"), nullable=False)
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)
    pattern_type: Mapped[str] = mapped_column(String(20), nullable=False, default="exact", server_default=text("'exact'::character varying"))
    factor_id: Mapped[int | None] = mapped_column(ForeignKey("tb_evaluation_factors.factor_id", ondelete="RESTRICT"))
    weight: Mapped[float | None] = mapped_column(Numeric(3, 2), default=1.0, server_default=text("1.0"))
    source: Mapped[str | None] = mapped_column(String(30), default="manual", server_default=text("'manual'::character varying"))
    example_context: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_lk_level_active", "level_id", "is_active"),
        # init.sql 보유 — ORM 동기화 (drift 방지)
        Index(
            "idx_lk_keyword_trgm",
            "keyword",
            postgresql_using="gin",
            postgresql_ops={"keyword": "gin_trgm_ops"},
        ),
    )


# ============================================================
# [C] 문서
# ============================================================

class Document(Base):
    __tablename__ = "tb_documents"

    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    external_ref: Mapped[str | None] = mapped_column(String(100))
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    source_format: Mapped[str] = mapped_column(String(10), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    file_hash: Mapped[str | None] = mapped_column(String(64))

    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict, server_default=text("'{}'::jsonb"))

    raw_text_uri: Mapped[str | None] = mapped_column(String(500))
    normalized_text_uri: Mapped[str | None] = mapped_column(String(500))
    text_preview: Mapped[str | None] = mapped_column(String(2000))
    char_count: Mapped[int | None] = mapped_column(Integer)

    extraction_method: Mapped[str | None] = mapped_column(String(30), default="parser", server_default=text("'parser'::character varying"))
    extraction_quality: Mapped[float | None] = mapped_column(Numeric(3, 2))
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))

    processing_status: Mapped[str] = mapped_column(String(20), default="pending", server_default=text("'pending'::character varying"))
    error_message: Mapped[str | None] = mapped_column(Text)

    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(50))

    # #38 soft-delete/보존정책 — 논리 삭제 시각. NULL이면 활성(미삭제) 행.
    # 기본 None이라 기존 행/테스트 비파괴. 물리 delete/cascade는 그대로 두고
    # soft_delete()가 이 값을 NOW()로 세팅, 조회 메서드는 NULL만 노출한다.
    # 실제 보존기간 만료 후 purge(물리삭제) 잡은 본 작업 범위 밖(운영 정책).
    deleted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True, default=None)

    __table_args__ = (
        # idx_doc_status — tenant 제거로 idx_doc_tenant_status 의 prefix 컬럼만 잔존.
        Index("idx_doc_status", "processing_status"),
        Index("idx_doc_format", "source_format"),
        Index("idx_doc_uploaded", desc("uploaded_at")),
        # init.sql 보유 — ORM 동기화 (drift 방지).
        # tenant 제거: file_hash 단독 UNIQUE(중복 업로드 dedup). 격리는 KL 포털 전담.
        Index(
            "idx_doc_hash",
            "file_hash",
            unique=True,
            postgresql_where=text("file_hash IS NOT NULL"),
        ),
        Index(
            "idx_doc_metadata",
            "metadata",
            postgresql_using="gin",
            postgresql_ops={"metadata": "jsonb_path_ops"},
        ),
        Index(
            "idx_doc_pending",
            "uploaded_at",
            postgresql_where=text("processing_status = 'pending'"),
        ),
    )


class Chunk(Base):
    """청크 파티션 부모. 실제 INSERT는 월별 子 파티션으로 자동 라우팅."""
    __tablename__ = "tb_chunks"

    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), server_default=func.gen_random_uuid())
    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int | None] = mapped_column(SmallInteger)
    page_end: Mapped[int | None] = mapped_column(SmallInteger)
    section_path: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    overlap_prev: Mapped[int | None] = mapped_column(SmallInteger, default=0, server_default=text("0"))
    overlap_next: Mapped[int | None] = mapped_column(SmallInteger, default=0, server_default=text("0"))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        PrimaryKeyConstraint("chunk_id", "created_at"),
        Index("idx_chunk_doc", "doc_id", "chunk_index"),
        # init.sql의 PARTITION BY RANGE (created_at)는 ORM이 관리하지 않음.
        # SQLAlchemy의 declarative로는 표현이 부정확해 DB측에만 둠.
    )


# ============================================================
# [D] 라벨링
# ============================================================

class DocumentLabel(Base):
    __tablename__ = "tb_document_labels"

    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tb_documents.doc_id", ondelete="CASCADE"), primary_key=True)
    level_id: Mapped[int] = mapped_column(ForeignKey("tb_classification_levels.level_id", ondelete="RESTRICT"), nullable=False)
    labeled_by: Mapped[str] = mapped_column(String(30), nullable=False)
    labeler_id: Mapped[str | None] = mapped_column(String(50))
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2))
    total_score: Mapped[float | None] = mapped_column(Numeric(4, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    verified_by: Mapped[str | None] = mapped_column(String(50))
    labeled_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_dl_level", "level_id"),
        Index("idx_dl_labeled_by", "labeled_by"),
    )


class DocumentFactorScore(Base):
    __tablename__ = "tb_document_factor_scores"

    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tb_documents.doc_id", ondelete="CASCADE"), primary_key=True)
    factor_id: Mapped[int] = mapped_column(ForeignKey("tb_evaluation_factors.factor_id", ondelete="RESTRICT"), primary_key=True)
    score: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False)

    __table_args__ = (
        # 정본 B안 3요건(S·V·M)은 0·1·2점 — migration c7d8e9f0a1b2가 DB CHECK를
        # 0~5에서 0~2로 교체. ORM도 동기화(이전엔 0~5로 drift). 제약명도 DB와 일치.
        CheckConstraint("score >= 0 AND score <= 2", name="ck_dfs_score_0_2"),
    )


# ============================================================
# [E] 추론
# ============================================================

class Classification(Base):
    __tablename__ = "tb_classifications"

    classification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tb_documents.doc_id", ondelete="RESTRICT"), nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    predicted_level_id: Mapped[int] = mapped_column(ForeignKey("tb_classification_levels.level_id", ondelete="RESTRICT"), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    alternatives: Mapped[list] = mapped_column(JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb"))
    aggregation_method: Mapped[str | None] = mapped_column(String(20), default="hybrid", server_default=text("'hybrid'::character varying"))
    chunk_count: Mapped[int | None] = mapped_column(SmallInteger)
    rag_used: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    rag_top_k: Mapped[int | None] = mapped_column(SmallInteger)
    rag_agreement: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(20), default="staging", server_default=text("'staging'::character varying"))
    inference_ms: Mapped[int | None] = mapped_column(Integer)
    classified_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_cls_doc", "doc_id", desc("classified_at")),
        # idx_cls_status — tenant 제거로 idx_cls_tenant_status 의 status 컬럼만 잔존.
        Index("idx_cls_status", "status"),
        Index("idx_cls_model_level", "model_version", "predicted_level_id", "status"),
        # init.sql 보유 — ORM 동기화 (drift 방지)
        Index(
            "idx_cls_staging",
            desc("classified_at"),
            postgresql_where=text("status = 'staging'"),
        ),
        # 최근 분류 시계열 조회 hot path (tenant 제거 — 전역 스코프).
        Index("idx_cls_classified", "classified_at"),
    )


class ClassificationEvidence(Base):
    __tablename__ = "tb_classification_evidence"

    evidence_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    classification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tb_classifications.classification_id", ondelete="CASCADE"), nullable=False)
    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(20), nullable=False)
    factor_id: Mapped[int | None] = mapped_column(ForeignKey("tb_evaluation_factors.factor_id", ondelete="RESTRICT"))
    excerpt: Mapped[str] = mapped_column(Text, nullable=False)
    excerpt_start: Mapped[int | None] = mapped_column(Integer)
    excerpt_end: Mapped[int | None] = mapped_column(Integer)
    contribution: Mapped[float] = mapped_column(Numeric(4, 3), nullable=False)
    attention_scores: Mapped[dict | None] = mapped_column(JSONB)
    rag_ref_doc_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    rag_similarity: Mapped[float | None] = mapped_column(Numeric(4, 3))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_ce_classification", "classification_id"),
        Index("idx_ce_chunk_contrib", "chunk_id", desc("contribution")),
    )


# ============================================================
# [F] 학습
# ============================================================

class ModelVersion(Base):
    __tablename__ = "tb_model_versions"

    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    version_label: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    base_model: Mapped[str] = mapped_column(String(100), nullable=False)
    model_type: Mapped[str | None] = mapped_column(String(20), default="classifier", server_default=text("'classifier'::character varying"))
    trained_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    training_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    training_data_count: Mapped[int | None] = mapped_column(Integer)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    model_uri: Mapped[str | None] = mapped_column(String(500))
    model_size_mb: Mapped[int | None] = mapped_column(Integer)
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    activated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    rolled_back_from: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tb_model_versions.version_id"))
    rollback_reason: Mapped[str | None] = mapped_column(Text)
    level_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # init.sql 보유 — ORM 동기화 (drift 방지)
        Index(
            "idx_mv_active",
            "is_active",
            unique=True,
            postgresql_where=text("is_active = TRUE"),
        ),
        Index("idx_mv_mlflow", "mlflow_run_id"),
    )


class TrainingRun(Base):
    __tablename__ = "tb_training_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    model_version: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tb_model_versions.version_id"))
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="queued", server_default=text("'queued'::character varying"))
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    duration_sec: Mapped[int | None] = mapped_column(Integer)
    total_samples: Mapped[int] = mapped_column(Integer, nullable=False)
    train_count: Mapped[int | None] = mapped_column(Integer)
    val_count: Mapped[int | None] = mapped_column(Integer)
    test_count: Mapped[int | None] = mapped_column(Integer)
    split_method: Mapped[str | None] = mapped_column(String(30), default="stratified", server_default=text("'stratified'::character varying"))
    split_seed: Mapped[int | None] = mapped_column(Integer)
    hyperparameters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    gpu_info: Mapped[dict | None] = mapped_column(JSONB)
    final_metrics: Mapped[dict | None] = mapped_column(JSONB)
    trigger_type: Mapped[str | None] = mapped_column(String(30), default="manual", server_default=text("'manual'::character varying"))
    trigger_ref: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(50))

    __table_args__ = (
        Index("idx_tr_model", "model_version"),
        Index("idx_tr_status", "status"),
        Index("idx_tr_date", desc("started_at")),
        # N4 신규 — list_recent_runs() ORDER BY created_at DESC hot path
        Index("idx_tr_created", "created_at"),
    )


class TrainingEpoch(Base):
    __tablename__ = "tb_training_epochs"

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tb_training_runs.run_id", ondelete="CASCADE"), primary_key=True)
    epoch: Mapped[int] = mapped_column(Integer, primary_key=True)
    train_loss: Mapped[float | None] = mapped_column(REAL)
    val_loss: Mapped[float | None] = mapped_column(REAL)
    val_metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict, server_default=text("'{}'::jsonb"))
    learning_rate: Mapped[float | None] = mapped_column(REAL)
    logged_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrainingDataset(Base):
    __tablename__ = "tb_training_datasets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tb_training_runs.run_id", ondelete="CASCADE"), nullable=False)
    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tb_documents.doc_id", ondelete="RESTRICT"), nullable=False)
    split_type: Mapped[str] = mapped_column(String(10), nullable=False)
    level_id: Mapped[int] = mapped_column(ForeignKey("tb_classification_levels.level_id", ondelete="RESTRICT"), nullable=False)

    __table_args__ = (
        # DB 실명은 PG 가 자동 생성한 tb_training_datasets_run_id_doc_id_key 다(실측 pg_constraint).
        # 이름을 바꾸는 것은 DB 변경이므로 선언을 실제에 맞춘다.
        UniqueConstraint("run_id", "doc_id", name="tb_training_datasets_run_id_doc_id_key"),
        Index("idx_td_run_split", "run_id", "split_type"),
        Index("idx_td_doc", "doc_id"),
    )


# ============================================================
# [G] 보정 — Active Learning 진실 소스
# ============================================================

class Correction(Base):
    __tablename__ = "tb_corrections"

    correction_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    classification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("tb_classifications.classification_id", ondelete="CASCADE"), nullable=False)
    original_level_id: Mapped[int] = mapped_column(ForeignKey("tb_classification_levels.level_id", ondelete="RESTRICT"), nullable=False)
    corrected_level_id: Mapped[int] = mapped_column(ForeignKey("tb_classification_levels.level_id", ondelete="RESTRICT"), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    corrected_by: Mapped[str] = mapped_column(String(50), nullable=False)
    corrected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    consumed_in_run: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tb_training_runs.run_id"))
    consumed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        # #26b 최종 방어선 — 같은 (분류·보정등급·보정자) 조합 중복 보정 행 방지.
        # confirm/보정 경로가 select-then-insert일 때 race로 중복이 생기면
        # active learning 라벨이 중복 가중되므로 DB UNIQUE로 막는다.
        UniqueConstraint(
            "classification_id",
            "corrected_level_id",
            "corrected_by",
            name="uq_corr_cls_level_by",
        ),
        Index("idx_corr_cls", "classification_id"),
        Index("idx_corr_direction", "direction"),
        Index("idx_corr_at", desc("corrected_at")),
        # init.sql 보유 — ORM 동기화 (drift 방지)
        # unconsumed_corrections() WHERE consumed_in_run IS NULL hot path
        Index(
            "idx_corr_unconsumed",
            "consumed_in_run",
            postgresql_where=text("consumed_in_run IS NULL"),
        ),
    )


# ============================================================
# [H] 샘플 생성
# ============================================================

class PromptVersion(Base):
    __tablename__ = "tb_prompt_versions"

    prompt_version: Mapped[str] = mapped_column(String(30), primary_key=True)
    chain_stage: Mapped[str] = mapped_column(String(20), nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    avg_quality_score: Mapped[float | None] = mapped_column(Numeric(3, 2))
    usage_count: Mapped[int | None] = mapped_column(Integer, default=0, server_default=text("0"))
    approval_rate: Mapped[float | None] = mapped_column(Numeric(3, 2))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(50))
    notes: Mapped[str | None] = mapped_column(Text)


class SampleDocument(Base):
    __tablename__ = "tb_sample_documents"

    sample_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    doc_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("tb_documents.doc_id"))
    target_level_id: Mapped[int] = mapped_column(ForeignKey("tb_classification_levels.level_id", ondelete="RESTRICT"), nullable=False)
    doc_type: Mapped[str | None] = mapped_column(String(50))
    outline_prompt_version: Mapped[str | None] = mapped_column(ForeignKey("tb_prompt_versions.prompt_version"))
    body_prompt_version: Mapped[str | None] = mapped_column(ForeignKey("tb_prompt_versions.prompt_version"))
    qc_prompt_version: Mapped[str | None] = mapped_column(ForeignKey("tb_prompt_versions.prompt_version"))
    llm_provider: Mapped[str] = mapped_column(String(30), nullable=False)
    llm_model: Mapped[str] = mapped_column(String(50), nullable=False)
    generated_outline: Mapped[str | None] = mapped_column(Text)
    generated_content: Mapped[str] = mapped_column(Text, nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Numeric(3, 2))
    quality_report: Mapped[dict | None] = mapped_column(JSONB)
    # [P0#1] 본문 출처 마커 — 생성기(SynthDoc.label_source)에서 보존. None=정상 JSON 생성,
    # "noop_fallback"=placeholder 본문(학습 편입 금지), "llm_nonjson"=실 LLM 비-JSON 응답.
    # 워커가 이 마커 없이 list[dict]만 반환하던 시절엔 검수큐 적재 자체가 없어 마커도 소실됐다.
    label_source: Mapped[str | None] = mapped_column(String(30))
    parse_error: Mapped[str | None] = mapped_column(Text)
    review_status: Mapped[str | None] = mapped_column(String(20), default="pending_review", server_default=text("'pending_review'::character varying"))
    reviewed_by: Mapped[str | None] = mapped_column(String(50))
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_sd_status", "review_status"),
        Index("idx_sd_level", "target_level_id"),
        # N4 신규 — list_pending_review() WHERE review_status=? ORDER BY created_at hot path
        Index("idx_sd_status_created", "review_status", "created_at"),
    )


# ============================================================
# [I] 비용 (월별 파티션 부모)
# ============================================================

class LlmUsage(Base):
    """월별 파티션 부모. INSERT는 called_at 기준 자동 라우팅."""
    __tablename__ = "tb_llm_usage"

    usage_id: Mapped[int] = mapped_column(BigInteger, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    purpose: Mapped[str] = mapped_column(String(30), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(20))
    reference_id: Mapped[str | None] = mapped_column(String(100))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    # total_tokens는 DB측 generated column. ORM은 server_default 미설정으로 read-only처럼 처리.
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    cost_krw: Mapped[float | None] = mapped_column(Numeric(12, 2))
    billing_phase: Mapped[str] = mapped_column(String(20), nullable=False, default="development", server_default=text("'development'::character varying"))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    error_code: Mapped[str | None] = mapped_column(String(50))
    called_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        PrimaryKeyConstraint("usage_id", "called_at"),
        Index("idx_lu_phase", "billing_phase", desc("called_at")),
        Index("idx_lu_purpose", "purpose"),
        Index("idx_lu_ref", "reference_type", "reference_id"),
    )


# ============================================================
# [J] 감사 로그 (월별 파티션 부모)
# ============================================================

class AuditLog(Base):
    """월별 파티션 부모. 모든 API 호출 기록 (영업비밀 시스템 필수, doc/04 §9.5)."""
    __tablename__ = "tb_audit_log"

    audit_id: Mapped[int] = mapped_column(BigInteger, autoincrement=True)
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    actor_id: Mapped[str | None] = mapped_column(String(50))
    actor_role: Mapped[str | None] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(30))
    target_id: Mapped[str | None] = mapped_column(String(100))
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    success: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    error_code: Mapped[str | None] = mapped_column(String(50))
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        PrimaryKeyConstraint("audit_id", "occurred_at"),
        Index("idx_audit_actor", "actor_id", desc("occurred_at")),
        Index("idx_audit_target", "target_type", "target_id"),
        Index("idx_audit_action", "action", desc("occurred_at")),
    )


# ============================================================
# [K] 가이드 문서 버전 이력
# ============================================================

class Guide(Base):
    """가이드 문서 업로드 이력 — GuideService in-memory 대체."""
    __tablename__ = "tb_guides"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    guide_id: Mapped[str] = mapped_column(String(200), nullable=False)
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    effective_date: Mapped[str | None] = mapped_column(String(30))
    change_summary: Mapped[str | None] = mapped_column(Text)
    doc_type: Mapped[str | None] = mapped_column(String(50))
    filename: Mapped[str | None] = mapped_column(String(500))
    indexed: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    embedding_vector_count: Mapped[int] = mapped_column(Integer, default=0, server_default=text("0"))
    index_name: Mapped[str | None] = mapped_column(String(300))
    alias: Mapped[str | None] = mapped_column(String(300))
    model: Mapped[str | None] = mapped_column(String(100))
    registered_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        # 동시 업로드 시 중복 버전 행 방지(최종 방어선) — GuideRepo.upsert의
        # select-then-insert는 락이 없어 race에서 중복을 만들 수 있다.
        # tenant 제거: (guide_id, version) 전역 UNIQUE. 격리는 KL 포털 전담.
        UniqueConstraint("guide_id", "version", name="uq_guides_id_version"),
        Index("idx_guides_guide_id", "guide_id"),
        Index("idx_guides_registered", "registered_at"),
    )


__all__ = [
    "ClassificationLevel",
    "EvaluationFactor",
    "LevelKeyword",
    "Document",
    "Chunk",
    "DocumentLabel",
    "DocumentFactorScore",
    "Classification",
    "ClassificationEvidence",
    "ModelVersion",
    "TrainingRun",
    "TrainingEpoch",
    "TrainingDataset",
    "Correction",
    "PromptVersion",
    "SampleDocument",
    "LlmUsage",
    "AuditLog",
    "Guide",
]
