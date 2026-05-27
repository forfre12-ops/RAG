"""17테이블 ORM 매핑 (init.sql v2 1:1).

도메인 그룹:
  A 테넌트:       Tenant
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
- v_classification_final 등 운영 뷰는 raw SQL이나 별도 read-only 모델로 다룸.
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
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from lloydk.db.session import Base


# ============================================================
# [A] 테넌트
# ============================================================

class Tenant(Base):
    __tablename__ = "tenants"

    tenant_id: Mapped[str] = mapped_column(String(50), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    rag_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    active_model_version: Mapped[str | None] = mapped_column(String(50))
    api_key_hash: Mapped[str | None] = mapped_column(String(128))
    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ============================================================
# [B] 등급체계
# ============================================================

class ClassificationLevel(Base):
    __tablename__ = "classification_levels"

    level_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level_code: Mapped[str] = mapped_column(String(20), nullable=False, unique=True)
    level_name: Mapped[str] = mapped_column(String(50), nullable=False)
    level_order: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    color_hex: Mapped[str | None] = mapped_column(String(7), default="#808080")
    loss_weight: Mapped[float | None] = mapped_column(Numeric(4, 2), default=1.0)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(50))

    __table_args__ = (
        Index("idx_cl_active_order", "is_active", "level_order"),
    )


class EvaluationFactor(Base):
    __tablename__ = "evaluation_factors"

    factor_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    factor_code: Mapped[str] = mapped_column(String(30), nullable=False, unique=True)
    factor_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    weight: Mapped[float] = mapped_column(Numeric(3, 2), nullable=False, default=0.25)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LevelKeyword(Base):
    __tablename__ = "level_keywords"

    keyword_id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    level_id: Mapped[int] = mapped_column(ForeignKey("classification_levels.level_id", ondelete="RESTRICT"), nullable=False)
    keyword: Mapped[str] = mapped_column(String(200), nullable=False)
    pattern_type: Mapped[str] = mapped_column(String(20), nullable=False, default="exact")
    factor_id: Mapped[int | None] = mapped_column(ForeignKey("evaluation_factors.factor_id", ondelete="RESTRICT"))
    weight: Mapped[float | None] = mapped_column(Numeric(3, 2), default=1.0)
    source: Mapped[str | None] = mapped_column(String(30), default="manual")
    example_context: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_lk_level_active", "level_id", "is_active"),
    )


# ============================================================
# [C] 문서
# ============================================================

class Document(Base):
    __tablename__ = "documents"

    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"), nullable=False)
    external_ref: Mapped[str | None] = mapped_column(String(100))
    filename: Mapped[str] = mapped_column(String(500), nullable=False)
    source_format: Mapped[str] = mapped_column(String(10), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    file_hash: Mapped[str | None] = mapped_column(String(64))

    metadata_: Mapped[dict] = mapped_column("metadata", JSONB, default=dict)

    raw_text_uri: Mapped[str | None] = mapped_column(String(500))
    normalized_text_uri: Mapped[str | None] = mapped_column(String(500))
    text_preview: Mapped[str | None] = mapped_column(String(2000))
    char_count: Mapped[int | None] = mapped_column(Integer)

    extraction_method: Mapped[str | None] = mapped_column(String(30), default="parser")
    extraction_quality: Mapped[float | None] = mapped_column(Numeric(3, 2))
    ocr_used: Mapped[bool] = mapped_column(Boolean, default=False)

    processing_status: Mapped[str] = mapped_column(String(20), default="pending")
    error_message: Mapped[str | None] = mapped_column(Text)

    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column(String(50))

    __table_args__ = (
        Index("idx_doc_tenant_status", "tenant_id", "processing_status"),
        Index("idx_doc_format", "source_format"),
        Index("idx_doc_uploaded", "uploaded_at"),
    )


class Chunk(Base):
    """청크 파티션 부모. 실제 INSERT는 월별 子 파티션으로 자동 라우팅."""
    __tablename__ = "chunks"

    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), server_default=func.gen_random_uuid())
    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    tenant_id: Mapped[str] = mapped_column(String(50), nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[int | None] = mapped_column(SmallInteger)
    page_end: Mapped[int | None] = mapped_column(SmallInteger)
    section_path: Mapped[list[str] | None] = mapped_column(ARRAY(Text))
    overlap_prev: Mapped[int | None] = mapped_column(SmallInteger, default=0)
    overlap_next: Mapped[int | None] = mapped_column(SmallInteger, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        PrimaryKeyConstraint("chunk_id", "created_at"),
        Index("idx_chunk_doc", "doc_id", "chunk_index"),
        Index("idx_chunk_tenant", "tenant_id"),
        # init.sql의 PARTITION BY RANGE (created_at)는 ORM이 관리하지 않음.
        # SQLAlchemy의 declarative로는 표현이 부정확해 DB측에만 둠.
    )


# ============================================================
# [D] 라벨링
# ============================================================

class DocumentLabel(Base):
    __tablename__ = "document_labels"

    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.doc_id", ondelete="CASCADE"), primary_key=True)
    level_id: Mapped[int] = mapped_column(ForeignKey("classification_levels.level_id", ondelete="RESTRICT"), nullable=False)
    labeled_by: Mapped[str] = mapped_column(String(30), nullable=False)
    labeler_id: Mapped[str | None] = mapped_column(String(50))
    confidence: Mapped[float | None] = mapped_column(Numeric(3, 2))
    total_score: Mapped[float | None] = mapped_column(Numeric(4, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    verified_by: Mapped[str | None] = mapped_column(String(50))
    labeled_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_dl_level", "level_id"),
        Index("idx_dl_labeled_by", "labeled_by"),
    )


class DocumentFactorScore(Base):
    __tablename__ = "document_factor_scores"

    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.doc_id", ondelete="CASCADE"), primary_key=True)
    factor_id: Mapped[int] = mapped_column(ForeignKey("evaluation_factors.factor_id", ondelete="RESTRICT"), primary_key=True)
    score: Mapped[float] = mapped_column(Numeric(4, 2), nullable=False)

    __table_args__ = (
        CheckConstraint("score >= 0 AND score <= 5", name="ck_dfs_score_range"),
    )


# ============================================================
# [E] 추론
# ============================================================

class Classification(Base):
    __tablename__ = "classifications"

    classification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.doc_id", ondelete="RESTRICT"), nullable=False)
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"), nullable=False)
    model_version: Mapped[str] = mapped_column(String(50), nullable=False)
    predicted_level_id: Mapped[int] = mapped_column(ForeignKey("classification_levels.level_id", ondelete="RESTRICT"), nullable=False)
    confidence: Mapped[float] = mapped_column(Numeric(5, 4), nullable=False)
    alternatives: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    aggregation_method: Mapped[str | None] = mapped_column(String(20), default="hybrid")
    chunk_count: Mapped[int | None] = mapped_column(SmallInteger)
    rag_used: Mapped[bool] = mapped_column(Boolean, default=False)
    rag_top_k: Mapped[int | None] = mapped_column(SmallInteger)
    rag_agreement: Mapped[bool | None] = mapped_column(Boolean)
    status: Mapped[str] = mapped_column(String(20), default="staging")
    inference_ms: Mapped[int | None] = mapped_column(Integer)
    classified_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_cls_doc", "doc_id", "classified_at"),
        Index("idx_cls_tenant_status", "tenant_id", "status"),
        Index("idx_cls_model_level", "model_version", "predicted_level_id", "status"),
    )


class ClassificationEvidence(Base):
    __tablename__ = "classification_evidence"

    evidence_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    classification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("classifications.classification_id", ondelete="CASCADE"), nullable=False)
    chunk_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    evidence_type: Mapped[str] = mapped_column(String(20), nullable=False)
    factor_id: Mapped[int | None] = mapped_column(ForeignKey("evaluation_factors.factor_id", ondelete="RESTRICT"))
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
        Index("idx_ce_chunk_contrib", "chunk_id", "contribution"),
    )


# ============================================================
# [F] 학습
# ============================================================

class ModelVersion(Base):
    __tablename__ = "model_versions"

    version_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    version_label: Mapped[str] = mapped_column(String(50), nullable=False, unique=True)
    base_model: Mapped[str] = mapped_column(String(100), nullable=False)
    model_type: Mapped[str | None] = mapped_column(String(20), default="classifier")
    trained_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    training_run_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    training_data_count: Mapped[int | None] = mapped_column(Integer)
    metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    model_uri: Mapped[str | None] = mapped_column(String(500))
    model_size_mb: Mapped[int | None] = mapped_column(Integer)
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    activated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    deactivated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    rolled_back_from: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("model_versions.version_id"))
    rollback_reason: Mapped[str | None] = mapped_column(Text)
    level_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrainingRun(Base):
    __tablename__ = "training_runs"

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    model_version: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("model_versions.version_id"))
    mlflow_run_id: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    duration_sec: Mapped[int | None] = mapped_column(Integer)
    total_samples: Mapped[int] = mapped_column(Integer, nullable=False)
    train_count: Mapped[int | None] = mapped_column(Integer)
    val_count: Mapped[int | None] = mapped_column(Integer)
    test_count: Mapped[int | None] = mapped_column(Integer)
    split_method: Mapped[str | None] = mapped_column(String(30), default="stratified")
    split_seed: Mapped[int | None] = mapped_column(Integer)
    hyperparameters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    gpu_info: Mapped[dict | None] = mapped_column(JSONB)
    final_metrics: Mapped[dict | None] = mapped_column(JSONB)
    trigger_type: Mapped[str | None] = mapped_column(String(30), default="manual")
    trigger_ref: Mapped[str | None] = mapped_column(String(100))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(50))

    __table_args__ = (
        Index("idx_tr_model", "model_version"),
        Index("idx_tr_status", "status"),
        Index("idx_tr_date", "started_at"),
    )


class TrainingEpoch(Base):
    __tablename__ = "training_epochs"

    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("training_runs.run_id", ondelete="CASCADE"), primary_key=True)
    epoch: Mapped[int] = mapped_column(Integer, primary_key=True)
    train_loss: Mapped[float | None] = mapped_column(Numeric)
    val_loss: Mapped[float | None] = mapped_column(Numeric)
    val_metrics: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    learning_rate: Mapped[float | None] = mapped_column(Numeric)
    logged_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class TrainingDataset(Base):
    __tablename__ = "training_datasets"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("training_runs.run_id", ondelete="CASCADE"), nullable=False)
    doc_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.doc_id", ondelete="RESTRICT"), nullable=False)
    split_type: Mapped[str] = mapped_column(String(10), nullable=False)
    level_id: Mapped[int] = mapped_column(ForeignKey("classification_levels.level_id", ondelete="RESTRICT"), nullable=False)

    __table_args__ = (
        UniqueConstraint("run_id", "doc_id", name="uq_td_run_doc"),
        Index("idx_td_run_split", "run_id", "split_type"),
        Index("idx_td_doc", "doc_id"),
    )


# ============================================================
# [G] 보정 — Active Learning 진실 소스
# ============================================================

class Correction(Base):
    __tablename__ = "corrections"

    correction_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    classification_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("classifications.classification_id", ondelete="CASCADE"), nullable=False)
    original_level_id: Mapped[int] = mapped_column(ForeignKey("classification_levels.level_id", ondelete="RESTRICT"), nullable=False)
    corrected_level_id: Mapped[int] = mapped_column(ForeignKey("classification_levels.level_id", ondelete="RESTRICT"), nullable=False)
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    corrected_by: Mapped[str] = mapped_column(String(50), nullable=False)
    corrected_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    consumed_in_run: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("training_runs.run_id"))
    consumed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        Index("idx_corr_cls", "classification_id"),
        Index("idx_corr_direction", "direction"),
        Index("idx_corr_at", "corrected_at"),
    )


# ============================================================
# [H] 샘플 생성
# ============================================================

class PromptVersion(Base):
    __tablename__ = "prompt_versions"

    prompt_version: Mapped[str] = mapped_column(String(30), primary_key=True)
    chain_stage: Mapped[str] = mapped_column(String(20), nullable=False)
    template: Mapped[str] = mapped_column(Text, nullable=False)
    avg_quality_score: Mapped[float | None] = mapped_column(Numeric(3, 2))
    usage_count: Mapped[int | None] = mapped_column(Integer, default=0)
    approval_rate: Mapped[float | None] = mapped_column(Numeric(3, 2))
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column(String(50))
    notes: Mapped[str | None] = mapped_column(Text)


class SampleDocument(Base):
    __tablename__ = "sample_documents"

    sample_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=func.gen_random_uuid())
    doc_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("documents.doc_id"))
    tenant_id: Mapped[str] = mapped_column(ForeignKey("tenants.tenant_id"), nullable=False)
    target_level_id: Mapped[int] = mapped_column(ForeignKey("classification_levels.level_id", ondelete="RESTRICT"), nullable=False)
    doc_type: Mapped[str | None] = mapped_column(String(50))
    outline_prompt_version: Mapped[str | None] = mapped_column(ForeignKey("prompt_versions.prompt_version"))
    body_prompt_version: Mapped[str | None] = mapped_column(ForeignKey("prompt_versions.prompt_version"))
    qc_prompt_version: Mapped[str | None] = mapped_column(ForeignKey("prompt_versions.prompt_version"))
    llm_provider: Mapped[str] = mapped_column(String(30), nullable=False)
    llm_model: Mapped[str] = mapped_column(String(50), nullable=False)
    generated_outline: Mapped[str | None] = mapped_column(Text)
    generated_content: Mapped[str] = mapped_column(Text, nullable=False)
    quality_score: Mapped[float | None] = mapped_column(Numeric(3, 2))
    quality_report: Mapped[dict | None] = mapped_column(JSONB)
    review_status: Mapped[str | None] = mapped_column(String(20), default="pending_review")
    reviewed_by: Mapped[str | None] = mapped_column(String(50))
    reviewed_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_sd_status", "review_status"),
        Index("idx_sd_level", "target_level_id"),
        Index("idx_sd_tenant", "tenant_id"),
    )


# ============================================================
# [I] 비용 (월별 파티션 부모)
# ============================================================

class LlmUsage(Base):
    """월별 파티션 부모. INSERT는 called_at 기준 자동 라우팅."""
    __tablename__ = "llm_usage"

    usage_id: Mapped[int] = mapped_column(BigInteger, autoincrement=True)
    provider: Mapped[str] = mapped_column(String(30), nullable=False)
    model: Mapped[str] = mapped_column(String(50), nullable=False)
    purpose: Mapped[str] = mapped_column(String(30), nullable=False)
    reference_type: Mapped[str | None] = mapped_column(String(20))
    reference_id: Mapped[str | None] = mapped_column(String(100))
    tenant_id: Mapped[str | None] = mapped_column(ForeignKey("tenants.tenant_id"))
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    # total_tokens는 DB측 generated column. ORM은 server_default 미설정으로 read-only처럼 처리.
    cost_usd: Mapped[float] = mapped_column(Numeric(10, 6), nullable=False)
    cost_krw: Mapped[float | None] = mapped_column(Numeric(12, 2))
    billing_phase: Mapped[str] = mapped_column(String(20), nullable=False, default="development")
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_code: Mapped[str | None] = mapped_column(String(50))
    called_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        PrimaryKeyConstraint("usage_id", "called_at"),
        Index("idx_lu_phase", "billing_phase", "called_at"),
        Index("idx_lu_purpose", "purpose"),
        Index("idx_lu_ref", "reference_type", "reference_id"),
    )


# ============================================================
# [J] 감사 로그 (월별 파티션 부모)
# ============================================================

class AuditLog(Base):
    """월별 파티션 부모. 모든 API 호출 기록 (영업비밀 시스템 필수, doc/04 §9.5)."""
    __tablename__ = "audit_log"

    audit_id: Mapped[int] = mapped_column(BigInteger, autoincrement=True)
    request_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    tenant_id: Mapped[str | None] = mapped_column(String(50))
    actor_id: Mapped[str | None] = mapped_column(String(50))
    actor_role: Mapped[str | None] = mapped_column(String(30))
    action: Mapped[str] = mapped_column(String(50), nullable=False)
    target_type: Mapped[str | None] = mapped_column(String(30))
    target_id: Mapped[str | None] = mapped_column(String(100))
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    ip_address: Mapped[str | None] = mapped_column(INET)
    user_agent: Mapped[str | None] = mapped_column(String(500))
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    error_code: Mapped[str | None] = mapped_column(String(50))
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        PrimaryKeyConstraint("audit_id", "occurred_at"),
        Index("idx_audit_actor", "actor_id", "occurred_at"),
        Index("idx_audit_target", "target_type", "target_id"),
        Index("idx_audit_action", "action", "occurred_at"),
    )


__all__ = [
    "Tenant",
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
]
