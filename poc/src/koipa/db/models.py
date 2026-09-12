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
- [2026-09-11] 표·칼럼 물리명 = KOIPA 표준용어집 이름(migration 7b3e9d2a4f10, 대응표 정본은
  db/standard_names.py). **파이썬 속성명은 옛 이름 그대로** 두고 mapped_column 첫 인자로 DB
  이름을 준다 — ORM 호출부·API 응답은 바뀌지 않는다. ⚠ __table_args__ 의 Index·제약·text()
  문자열은 속성명이 아니라 **DB 칼럼명**으로 찾는다.
"""

from __future__ import annotations

import datetime as dt
import uuid

from sqlalchemy import (
    Identity,
    BigInteger,
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    REAL,
    PrimaryKeyConstraint,
    Uuid,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    desc,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, INET, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from koipa.db.session import Base

# [2026-09-09] MariaDB 를 버리고 PostgreSQL 로 되돌리면서 .with_variant() 대체 타입을
# 걷었다 — 렌더링이 갈릴 dialect 가 없다. 별칭은 그대로 둔다(15곳이 이 이름을 쓴다).
# 다시 다른 dialect 를 태워야 하면 여기 한 줄에 .with_variant() 를 붙이면 된다.
_JSON_PORTABLE = JSONB()
_ARRAY_TEXT_PORTABLE = ARRAY(Text)
_INET_PORTABLE = INET()


# tenant 제거: 격리는 KL 포털 전담 (2026-06-24 멀티테넌트 전면 제거 결정).
# Koipa는 단일 고객사 엔진 — per-customer 경계는 상류(KL 포털 라우팅)가 보장.


# ============================================================
# [B] 등급체계
# ============================================================

class ClassificationLevel(Base):
    __tablename__ = "tad_cm_clsf_grd_mng"

    level_id: Mapped[int] = mapped_column("grd_sn", Integer, Identity(always=True), primary_key=True)
    level_code: Mapped[str] = mapped_column("grd_cd", String(20), nullable=False, unique=True)
    level_name: Mapped[str] = mapped_column("grd_nm", String(50), nullable=False)
    level_order: Mapped[int] = mapped_column("grd_seq", SmallInteger, nullable=False)
    description: Mapped[str | None] = mapped_column("clsf_grd_expln", Text)
    color_hex: Mapped[str | None] = mapped_column("colr_cd", String(7), default="#808080", server_default=text("'#808080'"))
    loss_weight: Mapped[float | None] = mapped_column("loss_wgvl_cfc", Numeric(4, 2), default=1.0, server_default=text("1.0"))
    is_active: Mapped[bool] = mapped_column("actvtn_yn", Boolean, default=True, server_default=text("true"))
    created_at: Mapped[dt.datetime] = mapped_column("crt_dt", DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column("mdfcn_dt", DateTime(timezone=True), server_default=func.now())
    created_by: Mapped[str | None] = mapped_column("creatr_id", String(50))

    __table_args__ = (
        Index("idx_cl_active_order", "actvtn_yn", "grd_seq"),
    )


class EvaluationFactor(Base):
    __tablename__ = "tad_em_evl_rqmt_mng"

    factor_id: Mapped[int] = mapped_column("rqmt_sn", Integer, Identity(always=True), primary_key=True)
    factor_code: Mapped[str] = mapped_column("rqmt_cd", String(30), nullable=False, unique=True)
    factor_name: Mapped[str] = mapped_column("rqmt_nm", String(100), nullable=False)
    description: Mapped[str | None] = mapped_column("evl_rqmt_expln", Text)
    weight: Mapped[float] = mapped_column("wgvl_cfc", Numeric(3, 2), nullable=False, default=0.25, server_default=text("0.25"))
    is_active: Mapped[bool] = mapped_column("actvtn_yn", Boolean, default=True, server_default=text("true"))
    created_at: Mapped[dt.datetime] = mapped_column("crt_dt", DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at: Mapped[dt.datetime] = mapped_column("mdfcn_dt", DateTime(timezone=True), server_default=func.now())


class LevelKeyword(Base):
    __tablename__ = "tad_gm_grd_kywd_mng"

    keyword_id: Mapped[int] = mapped_column("kywd_sn", Integer, Identity(always=True), primary_key=True)
    level_id: Mapped[int] = mapped_column("grd_sn", ForeignKey("tad_cm_clsf_grd_mng.grd_sn", ondelete="RESTRICT"), nullable=False)
    keyword: Mapped[str] = mapped_column("kywd_nm", String(200), nullable=False)
    pattern_type: Mapped[str] = mapped_column("ptn_type_nm", String(20), nullable=False, default="exact", server_default=text("'exact'"))
    factor_id: Mapped[int | None] = mapped_column("rqmt_sn", ForeignKey("tad_em_evl_rqmt_mng.rqmt_sn", ondelete="RESTRICT"))
    weight: Mapped[float | None] = mapped_column("wgvl_cfc", Numeric(3, 2), default=1.0, server_default=text("1.0"))
    source: Mapped[str | None] = mapped_column("src_nm", String(30), default="manual", server_default=text("'manual'"))
    is_active: Mapped[bool] = mapped_column("actvtn_yn", Boolean, default=True, server_default=text("true"))
    created_at: Mapped[dt.datetime] = mapped_column("crt_dt", DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_lk_level_active", "grd_sn", "actvtn_yn"),
        # init.sql 보유 — ORM 동기화 (drift 방지)
        Index(
            "idx_lk_keyword_trgm",
            "kywd_nm",
            postgresql_using="gin",
            postgresql_ops={"kywd_nm": "gin_trgm_ops"},
        ),
    )


# ============================================================
# [C] 문서
# ============================================================

class Document(Base):
    __tablename__ = "tad_dm_doc_mng"

    doc_id: Mapped[uuid.UUID] = mapped_column("doc_id", Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    external_ref: Mapped[str | None] = mapped_column("otsd_rfrnc_no", String(100))
    filename: Mapped[str] = mapped_column("file_nm", String(500), nullable=False)
    source_format: Mapped[str] = mapped_column("orgnl_frmat_nm", String(10), nullable=False)
    file_size_bytes: Mapped[int | None] = mapped_column("file_sz", BigInteger)
    file_hash: Mapped[str | None] = mapped_column("file_hash_nm", String(64))

    metadata_: Mapped[dict] = mapped_column("mtdt_dsctn", _JSON_PORTABLE, default=dict, server_default=text("'{}'"))

    raw_text_uri: Mapped[str | None] = mapped_column("orgtxt_path_nm", String(500))
    normalized_text_uri: Mapped[str | None] = mapped_column("nrmlz_txt_path_nm", String(500))
    text_preview: Mapped[str | None] = mapped_column("mtxt_prvw_cn", String(2000))
    char_count: Mapped[int | None] = mapped_column("char_cnt", Integer)

    extraction_method: Mapped[str | None] = mapped_column("extr_mth_nm", String(30), default="parser", server_default=text("'parser'"))
    extraction_quality: Mapped[float | None] = mapped_column("extr_qlty_scr", Numeric(3, 2))
    ocr_used: Mapped[bool] = mapped_column("ocr_use_yn", Boolean, default=False, server_default=text("false"))

    processing_status: Mapped[str] = mapped_column("prcs_stts_nm", String(20), default="pending", server_default=text("'pending'"))
    error_message: Mapped[str | None] = mapped_column("err_stts_msg_cn", Text)

    uploaded_at: Mapped[dt.datetime] = mapped_column("uld_dt", DateTime(timezone=True), server_default=func.now())
    processed_at: Mapped[dt.datetime | None] = mapped_column("prcs_cmptn_dt", DateTime(timezone=True))
    created_by: Mapped[str | None] = mapped_column("creatr_id", String(50))

    # #38 soft-delete/보존정책 — 논리 삭제 시각. NULL이면 활성(미삭제) 행.
    # 기본 None이라 기존 행/테스트 비파괴. 물리 delete/cascade는 그대로 두고
    # soft_delete()가 이 값을 NOW()로 세팅, 조회 메서드는 NULL만 노출한다.
    # 실제 보존기간 만료 후 purge(물리삭제) 잡은 본 작업 범위 밖(운영 정책).
    deleted_at: Mapped[dt.datetime | None] = mapped_column("del_dt", DateTime(timezone=True), nullable=True, default=None)

    __table_args__ = (
        # idx_doc_status — tenant 제거로 idx_doc_tenant_status 의 prefix 컬럼만 잔존.
        Index("idx_doc_status", "prcs_stts_nm"),
        Index("idx_doc_format", "orgnl_frmat_nm"),
        Index("idx_doc_uploaded", desc("uld_dt")),
        # init.sql 보유 — ORM 동기화 (drift 방지).
        # tenant 제거: file_hash 단독 UNIQUE(중복 업로드 dedup). 격리는 KL 포털 전담.
        Index(
            "idx_doc_hash",
            "file_hash_nm",
            unique=True,
            postgresql_where=text("file_hash_nm IS NOT NULL"),
        ),
        Index(
            "idx_doc_metadata",
            "mtdt_dsctn",
            postgresql_using="gin",
            postgresql_ops={"mtdt_dsctn": "jsonb_path_ops"},
        ),
        Index(
            "idx_doc_pending",
            "uld_dt",
            postgresql_where=text("prcs_stts_nm = 'pending'"),
        ),
    )


class Chunk(Base):
    """청크 파티션 부모. 실제 INSERT는 월별 子 파티션으로 자동 라우팅."""
    __tablename__ = "tad_cm_chnk_mng"

    chunk_id: Mapped[uuid.UUID] = mapped_column("chnk_id", Uuid(as_uuid=True), default=uuid.uuid4)
    doc_id: Mapped[uuid.UUID] = mapped_column("doc_id", Uuid(as_uuid=True), nullable=False)
    chunk_index: Mapped[int] = mapped_column("chnk_no", Integer, nullable=False)
    content: Mapped[str] = mapped_column("chnk_cn", Text, nullable=False)
    token_count: Mapped[int] = mapped_column("tkn_cnt", Integer, nullable=False)
    char_count: Mapped[int] = mapped_column("char_cnt", Integer, nullable=False)
    section_path: Mapped[list[str] | None] = mapped_column("sctn_path_nm", _ARRAY_TEXT_PORTABLE)
    overlap_prev: Mapped[int | None] = mapped_column("prev_chnk_ovlp_tkn_cnt", SmallInteger, default=0, server_default=text("0"))
    overlap_next: Mapped[int | None] = mapped_column("next_chnk_ovlp_tkn_cnt", SmallInteger, default=0, server_default=text("0"))
    created_at: Mapped[dt.datetime] = mapped_column("crt_dt", DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        PrimaryKeyConstraint("chnk_id", "crt_dt"),
        # 시간축 선두 인덱스 — tad_am_adt_log_mng 와 같은 사유.
        Index("idx_chunk_created", "crt_dt"),
        Index("idx_chunk_doc", "doc_id", "chnk_no"),
        # init.sql의 PARTITION BY RANGE (created_at)는 ORM이 관리하지 않음.
        # SQLAlchemy의 declarative로는 표현이 부정확해 DB측에만 둠.
    )


# ============================================================
# [D] 라벨링
# ============================================================

class DocumentLabel(Base):
    __tablename__ = "tad_dm_doc_lbl_mng"

    doc_id: Mapped[uuid.UUID] = mapped_column("doc_id", Uuid(as_uuid=True), ForeignKey("tad_dm_doc_mng.doc_id", ondelete="CASCADE"), primary_key=True)
    level_id: Mapped[int] = mapped_column("grd_sn", ForeignKey("tad_cm_clsf_grd_mng.grd_sn", ondelete="RESTRICT"), nullable=False)
    labeled_by: Mapped[str] = mapped_column("lbl_mnbd_nm", String(30), nullable=False)
    labeler_id: Mapped[str | None] = mapped_column("lbl_wrtr_id", String(50))
    # [2026-09-10] (3,2)→(5,4). 같은 이름의 tad_cm_clsf_rslt_mng.confidence 와 정밀도를 맞춘다
    # (감리 도표 75 · migration 5c1d9e0a7b34).
    confidence: Mapped[float | None] = mapped_column("rlbl_scr", Numeric(5, 4))
    total_score: Mapped[float | None] = mapped_column("tot_scr", Numeric(4, 2))
    notes: Mapped[str | None] = mapped_column("memo_dtl_cn", Text)
    is_verified: Mapped[bool] = mapped_column("vrfc_cmptn_yn", Boolean, default=False, server_default=text("false"))
    verified_by: Mapped[str | None] = mapped_column("vrfr_id", String(50))
    labeled_at: Mapped[dt.datetime] = mapped_column("lbl_dt", DateTime(timezone=True), server_default=func.now())
    verified_at: Mapped[dt.datetime | None] = mapped_column("vrfc_dt", DateTime(timezone=True))

    __table_args__ = (
        Index("idx_dl_level", "grd_sn"),
        Index("idx_dl_labeled_by", "lbl_mnbd_nm"),
    )


class DocumentFactorScore(Base):
    __tablename__ = "tad_dm_doc_rqmt_scr_mng"

    doc_id: Mapped[uuid.UUID] = mapped_column("doc_id", Uuid(as_uuid=True), ForeignKey("tad_dm_doc_mng.doc_id", ondelete="CASCADE"), primary_key=True)
    factor_id: Mapped[int] = mapped_column("rqmt_sn", ForeignKey("tad_em_evl_rqmt_mng.rqmt_sn", ondelete="RESTRICT"), primary_key=True)
    score: Mapped[float] = mapped_column("scr", Numeric(4, 2), nullable=False)

    __table_args__ = (
        # 정본 B안 3요건(S·V·M)은 0·1·2점 — migration c7d8e9f0a1b2가 DB CHECK를
        # 0~5에서 0~2로 교체. ORM도 동기화(이전엔 0~5로 drift). 제약명도 DB와 일치.
        CheckConstraint("scr >= 0 AND scr <= 2", name="ck_dfs_score_0_2"),
    )


# ============================================================
# [E] 추론
# ============================================================

class Classification(Base):
    __tablename__ = "tad_cm_clsf_rslt_mng"

    classification_id: Mapped[uuid.UUID] = mapped_column("clsf_id", Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doc_id: Mapped[uuid.UUID] = mapped_column("doc_id", Uuid(as_uuid=True), ForeignKey("tad_dm_doc_mng.doc_id", ondelete="RESTRICT"), nullable=False)
    model_version: Mapped[str] = mapped_column("mdl_ver_nm", String(50), nullable=False)
    predicted_level_id: Mapped[int] = mapped_column("predc_grd_sn", ForeignKey("tad_cm_clsf_grd_mng.grd_sn", ondelete="RESTRICT"), nullable=False)
    confidence: Mapped[float] = mapped_column("rlbl_scr", Numeric(5, 4), nullable=False)
    alternatives: Mapped[list] = mapped_column("nxtrnk_grd_list_cn", _JSON_PORTABLE, nullable=False, default=list, server_default=text("'[]'"))
    # 자동확정 정책 학습용 결정 시점 스냅샷. 기존 행은 원본 신호를 복원할 수 없어 NULL 유지.
    automation_assessment: Mapped[dict | None] = mapped_column("auto_cfmtn_evl_info_cn", _JSON_PORTABLE)
    aggregation_method: Mapped[str | None] = mapped_column("tot_mth_cd", String(20), default="hybrid", server_default=text("'hybrid'"))
    chunk_count: Mapped[int | None] = mapped_column("chnk_cnt", SmallInteger)
    status: Mapped[str] = mapped_column("clsf_stts_nm", String(20), default="staging", server_default=text("'staging'"))
    # 게이트 최종 결정을 생성 시점에 동결(status와 달리 이후 confirm/correction이 건드리지 않음).
    # nullable: 이 컬럼 도입 이전 행은 최초값을 복원할 수 없어 NULL로 남는다.
    initial_status: Mapped[str | None] = mapped_column("clsf_frst_stts_nm", String(20))
    inference_ms: Mapped[int | None] = mapped_column("infr_req_hr", Integer)
    classified_at: Mapped[dt.datetime] = mapped_column("clsf_dt", DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("idx_cls_doc", "doc_id", desc("clsf_dt")),
        # idx_cls_status — tenant 제거로 idx_cls_tenant_status 의 status 컬럼만 잔존.
        Index("idx_cls_status", "clsf_stts_nm"),
        Index("idx_cls_initial_status", "clsf_frst_stts_nm"),
        Index("idx_cls_model_level", "mdl_ver_nm", "predc_grd_sn", "clsf_stts_nm"),
        # init.sql 보유 — ORM 동기화 (drift 방지)
        Index(
            "idx_cls_staging",
            desc("clsf_dt"),
            postgresql_where=text("clsf_stts_nm = 'staging'"),
        ),
        # 최근 분류 시계열 조회 hot path (tenant 제거 — 전역 스코프).
        Index("idx_cls_classified", "clsf_dt"),
    )


class ClassificationEvidence(Base):
    __tablename__ = "tad_cm_clsf_bss_mng"

    evidence_id: Mapped[int] = mapped_column("bss_sn", BigInteger, Identity(always=True), primary_key=True)
    classification_id: Mapped[uuid.UUID] = mapped_column("clsf_id", Uuid(as_uuid=True), ForeignKey("tad_cm_clsf_rslt_mng.clsf_id", ondelete="CASCADE"), nullable=False)
    chunk_id: Mapped[uuid.UUID] = mapped_column("chnk_id", Uuid(as_uuid=True), nullable=False)
    evidence_type: Mapped[str] = mapped_column("bss_type_nm", String(20), nullable=False)
    factor_id: Mapped[int | None] = mapped_column("rqmt_sn", ForeignKey("tad_em_evl_rqmt_mng.rqmt_sn", ondelete="RESTRICT"))
    excerpt: Mapped[str] = mapped_column("exct_cn", Text, nullable=False)
    excerpt_start: Mapped[int | None] = mapped_column("exct_bgng_pstn_nm", Integer)
    excerpt_end: Mapped[int | None] = mapped_column("exct_end_pstn_nm", Integer)
    contribution: Mapped[float] = mapped_column("cndg_nvl", Numeric(4, 3), nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column("crt_dt", DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_ce_classification", "clsf_id"),
        Index("idx_ce_chunk_contrib", "chnk_id", desc("cndg_nvl")),
    )


# ============================================================
# [F] 학습
# ============================================================

class ModelVersion(Base):
    __tablename__ = "tad_mm_mdl_ver_mng"

    version_id: Mapped[uuid.UUID] = mapped_column("mdl_ver_id", Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    version_label: Mapped[str] = mapped_column("ver_lbl_nm", String(50), nullable=False, unique=True)
    base_model: Mapped[str] = mapped_column("base_mdl_nm", String(100), nullable=False)
    model_type: Mapped[str | None] = mapped_column("mdl_type_nm", String(20), default="classifier", server_default=text("'classifier'"))
    trained_at: Mapped[dt.datetime | None] = mapped_column("lrn_cmptn_dt", DateTime(timezone=True))
    training_run_id: Mapped[uuid.UUID | None] = mapped_column("lrn_excn_id", Uuid(as_uuid=True))
    training_data_count: Mapped[int | None] = mapped_column("lrn_data_nocs", Integer)
    metrics: Mapped[dict] = mapped_column("idct_info_cn", _JSON_PORTABLE, nullable=False, default=dict, server_default=text("'{}'"))
    model_uri: Mapped[str | None] = mapped_column("mdl_strg_path_nm", String(500))
    mlflow_run_id: Mapped[str | None] = mapped_column("flw_excn_id", String(64))
    is_active: Mapped[bool] = mapped_column("actvtn_yn", Boolean, default=False, server_default=text("false"))
    # [2026-09] MariaDB 는 부분 인덱스(WHERE)가 없다. "활성은 최대 1개" 불변식을 두 dialect
    # 모두에서 같은 방식으로 지키도록, is_active 대신 파생 칼럼에 유니크를 건다 — 활성일 때만
    # 1, 아니면 NULL. UNIQUE 인덱스는 NULL 을 여러 개 허용하므로 비활성 행은 몇 개든 공존하고
    # 활성 행만 하나로 묶인다. Postgres·MariaDB 양쪽에서 GENERATED ALWAYS AS ... STORED 로
    # 동일하게 동작함을 실측 확인했다(둘 다 두 번째 활성 삽입에서 IntegrityError).
    active_key: Mapped[int | None] = mapped_column(
        "actvtn_key", SmallInteger, Computed("CASE WHEN actvtn_yn THEN 1 END", persisted=True)
    )
    activated_at: Mapped[dt.datetime | None] = mapped_column("vtlz_dt", DateTime(timezone=True))
    deactivated_at: Mapped[dt.datetime | None] = mapped_column("dsbl_dt", DateTime(timezone=True))
    rolled_back_from: Mapped[uuid.UUID | None] = mapped_column("rlbk_src_ver_id", Uuid(as_uuid=True), ForeignKey("tad_mm_mdl_ver_mng.mdl_ver_id"))
    rollback_reason: Mapped[str | None] = mapped_column("rlbk_rsn", Text)
    level_snapshot: Mapped[dict | None] = mapped_column("grd_system_hstry_cn", _JSON_PORTABLE)
    created_at: Mapped[dt.datetime] = mapped_column("crt_dt", DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_mv_active", "actvtn_key", unique=True),
        Index("idx_mv_mlflow", "flw_excn_id"),
    )


class TrainingRun(Base):
    __tablename__ = "tad_lm_lrn_excn_mng"

    run_id: Mapped[uuid.UUID] = mapped_column("excn_id", Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # [2026-09-10] DB 칼럼명만 model_version_id 로 바꿨다(migration 5c1d9e0a7b34) — 분류결과의
    # model_version 은 모델 **라벨 문자열**인데 여기는 모델버전 표를 가리키는 **UUID FK** 라
    # 같은 이름에 뜻·형식이 달랐다. [2026-09-11] 표준 명명으로 mdl_ver_id(모델버전아이디),
    # 분류결과 쪽은 mdl_ver_nm(모델버전명)이 되어 이름으로도 갈린다. 파이썬 속성명은 그대로다.
    model_version: Mapped[uuid.UUID | None] = mapped_column(
        "mdl_ver_id", Uuid(as_uuid=True), ForeignKey("tad_mm_mdl_ver_mng.mdl_ver_id")
    )
    mlflow_run_id: Mapped[str | None] = mapped_column("flw_excn_id", String(64))
    status: Mapped[str] = mapped_column("lrn_excn_stts_cd", String(20), default="queued", server_default=text("'queued'"))
    started_at: Mapped[dt.datetime | None] = mapped_column("bgng_dt", DateTime(timezone=True))
    completed_at: Mapped[dt.datetime | None] = mapped_column("end_dt", DateTime(timezone=True))
    duration_sec: Mapped[int | None] = mapped_column("req_hr", Integer)
    total_samples: Mapped[int] = mapped_column("whol_sample_cnt", Integer, nullable=False)
    train_count: Mapped[int | None] = mapped_column("lrn_nocs", Integer)
    val_count: Mapped[int | None] = mapped_column("vrfc_nocs", Integer)
    test_count: Mapped[int | None] = mapped_column("test_nocs", Integer)
    split_method: Mapped[str | None] = mapped_column("prttn_mth_cd", String(30), default="stratified", server_default=text("'stratified'"))
    split_seed: Mapped[int | None] = mapped_column("prttn_seed", Integer)
    hyperparameters: Mapped[dict] = mapped_column("hprprm_info_cn", _JSON_PORTABLE, nullable=False, default=dict, server_default=text("'{}'"))
    final_metrics: Mapped[dict | None] = mapped_column("last_idct_info_cn", _JSON_PORTABLE)
    trigger_type: Mapped[str | None] = mapped_column("boot_mth_cd", String(30), default="manual", server_default=text("'manual'"))
    trigger_ref: Mapped[str | None] = mapped_column("boot_bss_idntfr_nm", String(100))
    error_message: Mapped[str | None] = mapped_column("err_stts_msg_cn", Text)
    created_at: Mapped[dt.datetime] = mapped_column("crt_dt", DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by: Mapped[str | None] = mapped_column("creatr_id", String(50))

    __table_args__ = (
        # 인덱스 칼럼은 DB 칼럼명으로 찾는다(파이썬 속성명 model_version 이 아니다).
        Index("idx_tr_model", "mdl_ver_id"),
        Index("idx_tr_status", "lrn_excn_stts_cd"),
        Index("idx_tr_date", desc("bgng_dt")),
        # N4 신규 — list_recent_runs() ORDER BY created_at DESC hot path
        Index("idx_tr_created", "crt_dt"),
    )


class TrainingEpoch(Base):
    __tablename__ = "tad_lm_lrn_epoch_mng"

    run_id: Mapped[uuid.UUID] = mapped_column("excn_id", Uuid(as_uuid=True), ForeignKey("tad_lm_lrn_excn_mng.excn_id", ondelete="CASCADE"), primary_key=True)
    epoch: Mapped[int] = mapped_column("epoch_sn", Integer, primary_key=True)
    train_loss: Mapped[float | None] = mapped_column("lrn_loss_nvl", REAL)
    val_loss: Mapped[float | None] = mapped_column("vrfc_loss_nvl", REAL)
    val_metrics: Mapped[dict] = mapped_column("vrfc_idct_info_cn", _JSON_PORTABLE, nullable=False, default=dict, server_default=text("'{}'"))
    learning_rate: Mapped[float | None] = mapped_column("lrnr", REAL)
    logged_at: Mapped[dt.datetime] = mapped_column("rcd_dt", DateTime(timezone=True), server_default=func.now())


class TrainingDataset(Base):
    __tablename__ = "tad_lm_lrn_datst_mng"

    id: Mapped[int] = mapped_column("lrn_datst_sn", BigInteger, Identity(always=True), primary_key=True)
    run_id: Mapped[uuid.UUID] = mapped_column("excn_id", Uuid(as_uuid=True), ForeignKey("tad_lm_lrn_excn_mng.excn_id", ondelete="CASCADE"), nullable=False)
    doc_id: Mapped[uuid.UUID] = mapped_column("doc_id", Uuid(as_uuid=True), ForeignKey("tad_dm_doc_mng.doc_id", ondelete="RESTRICT"), nullable=False)
    split_type: Mapped[str] = mapped_column("prttn_se_nm", String(10), nullable=False)
    level_id: Mapped[int] = mapped_column("grd_sn", ForeignKey("tad_cm_clsf_grd_mng.grd_sn", ondelete="RESTRICT"), nullable=False)

    __table_args__ = (
        # DB 실명은 PG 가 자동 생성한 tb_training_datasets_run_id_doc_id_key 다(실측 pg_constraint).
        # 이름을 바꾸는 것은 DB 변경이므로 선언을 실제에 맞춘다.
        UniqueConstraint("excn_id", "doc_id", name="tb_training_datasets_run_id_doc_id_key"),
        Index("idx_td_run_split", "excn_id", "prttn_se_nm"),
        Index("idx_td_doc", "doc_id"),
    )


# ============================================================
# [G] 보정 — Active Learning 진실 소스
# ============================================================

class Correction(Base):
    __tablename__ = "tad_cm_crct_mng"

    correction_id: Mapped[int] = mapped_column("crct_sn", BigInteger, Identity(always=True), primary_key=True)
    classification_id: Mapped[uuid.UUID] = mapped_column("clsf_id", Uuid(as_uuid=True), ForeignKey("tad_cm_clsf_rslt_mng.clsf_id", ondelete="CASCADE"), nullable=False)
    original_level_id: Mapped[int] = mapped_column("orgnl_grd_sn", ForeignKey("tad_cm_clsf_grd_mng.grd_sn", ondelete="RESTRICT"), nullable=False)
    corrected_level_id: Mapped[int] = mapped_column("cfmtn_grd_sn", ForeignKey("tad_cm_clsf_grd_mng.grd_sn", ondelete="RESTRICT"), nullable=False)
    direction: Mapped[str] = mapped_column("crct_ornt_nm", String(10), nullable=False)
    reason: Mapped[str | None] = mapped_column("crct_rsn", Text)
    corrected_by: Mapped[str] = mapped_column("clbtr_id", String(50), nullable=False)
    corrected_at: Mapped[dt.datetime] = mapped_column("crct_dt", DateTime(timezone=True), server_default=func.now())
    consumed_in_run: Mapped[uuid.UUID | None] = mapped_column("rflt_lrn_excn_id", Uuid(as_uuid=True), ForeignKey("tad_lm_lrn_excn_mng.excn_id"))
    consumed_at: Mapped[dt.datetime | None] = mapped_column("rflt_dt", DateTime(timezone=True))

    __table_args__ = (
        # #26b 최종 방어선 — 같은 (분류·보정등급·보정자) 조합 중복 보정 행 방지.
        # confirm/보정 경로가 select-then-insert일 때 race로 중복이 생기면
        # active learning 라벨이 중복 가중되므로 DB UNIQUE로 막는다.
        UniqueConstraint(
            "clsf_id",
            "cfmtn_grd_sn",
            "clbtr_id",
            name="uq_corr_cls_level_by",
        ),
        Index("idx_corr_cls", "clsf_id"),
        Index("idx_corr_direction", "crct_ornt_nm"),
        Index("idx_corr_at", desc("crct_dt")),
        # init.sql 보유 — ORM 동기화 (drift 방지)
        # unconsumed_corrections() WHERE consumed_in_run IS NULL hot path
        Index(
            "idx_corr_unconsumed",
            "rflt_lrn_excn_id",
            postgresql_where=text("rflt_lrn_excn_id IS NULL"),
        ),
    )


# ============================================================
# [H] 샘플 생성
# ============================================================

class PromptVersion(Base):
    __tablename__ = "tad_pm_prmpt_ver_mng"

    prompt_version: Mapped[str] = mapped_column("prmpt_ver_nm", String(30), primary_key=True)
    chain_stage: Mapped[str] = mapped_column("crt_stp_nm", String(20), nullable=False)
    template: Mapped[str] = mapped_column("tmplt_cn", Text, nullable=False)
    created_at: Mapped[dt.datetime] = mapped_column("crt_dt", DateTime(timezone=True), nullable=False, server_default=func.now())
    created_by: Mapped[str | None] = mapped_column("creatr_id", String(50))
    notes: Mapped[str | None] = mapped_column("prmpt_ver_memo_cn", Text)


class SampleDocument(Base):
    __tablename__ = "tad_sm_syn_doc_mng"

    sample_id: Mapped[uuid.UUID] = mapped_column("syn_doc_id", Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    doc_id: Mapped[uuid.UUID | None] = mapped_column("doc_id", Uuid(as_uuid=True), ForeignKey("tad_dm_doc_mng.doc_id"))
    target_level_id: Mapped[int] = mapped_column("goal_grd_sn", ForeignKey("tad_cm_clsf_grd_mng.grd_sn", ondelete="RESTRICT"), nullable=False)
    # 검수자가 승인하면서 고친 등급. NULL=교정 없음(target_level_id 그대로).
    # target_level_id 를 덮어쓰지 않는 이유: "생성 때 요구한 등급"과 "사람이 고친 등급"이
    # 한 칸에 뭉개지면 교정이 있었다는 사실 자체가 사라진다. 학습행 라벨은 이 값을 우선한다
    # (synthesis_service.build_training_rows).
    corrected_level_id: Mapped[int | None] = mapped_column("cfmtn_grd_sn", ForeignKey("tad_cm_clsf_grd_mng.grd_sn", ondelete="RESTRICT"))
    doc_type: Mapped[str | None] = mapped_column("doc_knd_nm", String(50))
    outline_prompt_version: Mapped[str | None] = mapped_column("otln_prmpt_ver_nm", ForeignKey("tad_pm_prmpt_ver_mng.prmpt_ver_nm"))
    body_prompt_version: Mapped[str | None] = mapped_column("mtxt_prmpt_ver_nm", ForeignKey("tad_pm_prmpt_ver_mng.prmpt_ver_nm"))
    qc_prompt_version: Mapped[str | None] = mapped_column("qlty_insp_prmpt_ver_nm", ForeignKey("tad_pm_prmpt_ver_mng.prmpt_ver_nm"))
    llm_provider: Mapped[str] = mapped_column("llm_offr_nm", String(30), nullable=False)
    llm_model: Mapped[str] = mapped_column("llm_mdl_nm", String(50), nullable=False)
    generated_outline: Mapped[str | None] = mapped_column("crt_otln", Text)
    generated_content: Mapped[str] = mapped_column("crt_mtxt_cn", Text, nullable=False)
    quality_score: Mapped[float | None] = mapped_column("qlty_scr", Numeric(3, 2))
    quality_report: Mapped[dict | None] = mapped_column("qlty_insp_rptp_cn", _JSON_PORTABLE)
    # [P0#1] 본문 출처 마커 — 생성기(SynthDoc.label_source)에서 보존. None=정상 JSON 생성,
    # "noop_fallback"=placeholder 본문(학습 편입 금지), "llm_nonjson"=실 LLM 비-JSON 응답.
    # 워커가 이 마커 없이 list[dict]만 반환하던 시절엔 검수큐 적재 자체가 없어 마커도 소실됐다.
    label_source: Mapped[str | None] = mapped_column("lbl_src_nm", String(30))
    parse_error: Mapped[str | None] = mapped_column("prsng_err_rsn", Text)
    review_status: Mapped[str | None] = mapped_column("igi_stts_cd", String(20), default="pending_review", server_default=text("'pending_review'"))
    reviewed_by: Mapped[str | None] = mapped_column("chckr_id", String(50))
    reviewed_at: Mapped[dt.datetime | None] = mapped_column("igi_dt", DateTime(timezone=True))
    rejection_reason: Mapped[str | None] = mapped_column("rjct_rsn", Text)

    created_at: Mapped[dt.datetime] = mapped_column("crt_dt", DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        Index("idx_sd_status", "igi_stts_cd"),
        Index("idx_sd_level", "goal_grd_sn"),
        # N4 신규 — list_pending_review() WHERE review_status=? ORDER BY created_at hot path
        Index("idx_sd_status_created", "igi_stts_cd", "crt_dt"),
    )


# ============================================================
# [I] 비용 (월별 파티션 부모)
# ============================================================

class LlmUsage(Base):
    """월별 파티션 부모. INSERT는 called_at 기준 자동 라우팅."""
    __tablename__ = "tad_lm_llm_usqty_mng"

    usage_id: Mapped[int] = mapped_column(
        "use_rcd_sn", BigInteger, Identity(always=True), autoincrement=True,
    )
    provider: Mapped[str] = mapped_column("offr_id", String(30), nullable=False)
    model: Mapped[str] = mapped_column("mdl_nm", String(50), nullable=False)
    purpose: Mapped[str] = mapped_column("clot_prps", String(30), nullable=False)
    reference_type: Mapped[str | None] = mapped_column("rfrnc_trgt_type_cd", String(20))
    reference_id: Mapped[str | None] = mapped_column("rfrnc_trgt_id", String(100))
    input_tokens: Mapped[int] = mapped_column("inpt_tkn_cnt", Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column("otpt_tkn_cnt", Integer, nullable=False)
    # total_tokens는 DB측 generated column. ORM은 server_default 미설정으로 read-only처럼 처리.
    cost_usd: Mapped[float] = mapped_column("usd_cst", Numeric(10, 6), nullable=False)
    cost_krw: Mapped[float | None] = mapped_column("kcur_cst", Numeric(12, 2))
    billing_phase: Mapped[str] = mapped_column("bllng_se_cd", String(20), nullable=False, default="development", server_default=text("'development'"))
    latency_ms: Mapped[int | None] = mapped_column("rspns_dly_hr", Integer)
    success: Mapped[bool] = mapped_column("scs_yn", Boolean, default=True, server_default=text("true"))
    error_code: Mapped[str | None] = mapped_column("err_cd", String(50))
    called_at: Mapped[dt.datetime] = mapped_column("clot_dt", DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        PrimaryKeyConstraint("use_rcd_sn", "clot_dt"),
        # 시간축 선두 인덱스 — tad_am_adt_log_mng 와 같은 사유(보존기간 삭제·기간 집계).
        Index("idx_lu_called", "clot_dt"),
        Index("idx_lu_phase", "bllng_se_cd", desc("clot_dt")),
        Index("idx_lu_purpose", "clot_prps"),
        Index("idx_lu_ref", "rfrnc_trgt_type_cd", "rfrnc_trgt_id"),
    )


# ============================================================
# [J] 감사 로그 (월별 파티션 부모)
# ============================================================

class AuditLog(Base):
    """월별 파티션 부모. 모든 API 호출 기록 (영업비밀 시스템 필수, doc/04 §9.5)."""
    __tablename__ = "tad_am_adt_log_mng"

    audit_id: Mapped[int] = mapped_column(
        "adt_sn", BigInteger, Identity(always=True), autoincrement=True,
    )
    request_id: Mapped[uuid.UUID | None] = mapped_column("dmnd_id", Uuid(as_uuid=True))
    actor_id: Mapped[str | None] = mapped_column("actr_id", String(50))
    actor_role: Mapped[str | None] = mapped_column("actr_role_nm", String(30))
    action: Mapped[str] = mapped_column("flfmt_bhvr_cd", String(50), nullable=False)
    target_type: Mapped[str | None] = mapped_column("trgt_type_cd", String(30))
    target_id: Mapped[str | None] = mapped_column("trgt_id", String(100))
    payload_hash: Mapped[str | None] = mapped_column("dmnd_mtxt_hash_cn", String(64))
    ip_address: Mapped[str | None] = mapped_column("dmnd_ip_addr", _INET_PORTABLE)
    user_agent: Mapped[str | None] = mapped_column("user_agnt_cn", String(500))
    success: Mapped[bool] = mapped_column("scs_yn", Boolean, default=True, server_default=text("true"))
    error_code: Mapped[str | None] = mapped_column("err_cd", String(50))
    occurred_at: Mapped[dt.datetime] = mapped_column("ocrn_dt", DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        PrimaryKeyConstraint("adt_sn", "ocrn_dt"),
        # [2026-09-05] 시간축 선두 인덱스. 감사 체인 검증(verify_chain)은
        # `WHERE occurred_at BETWEEN .. ORDER BY occurred_at, audit_id` 로 읽는데,
        # PK 는 (audit_id, occurred_at) 이고 나머지 인덱스는 occurred_at 이 두 번째라
        # 날짜 범위만으로는 어느 것도 못 탄다. PostgreSQL 에서는 파티션 프루닝이 그
        # 자리를 받고 있었으나 MariaDB 는 파티션을 쓰지 않기로 했다(2026-09-05) —
        # 프루닝이 없어진 자리를 이 인덱스가 받는다. 보존기간 삭제도 이걸 탄다.
        Index("idx_audit_occurred", "ocrn_dt", "adt_sn"),
        Index("idx_audit_actor", "actr_id", desc("ocrn_dt")),
        Index("idx_audit_target", "trgt_type_cd", "trgt_id"),
        Index("idx_audit_action", "flfmt_bhvr_cd", desc("ocrn_dt")),
    )


# ============================================================
# [K] 가이드 문서 버전 이력
# ============================================================

class AdvisoryLock(Base):
    """전역 직렬화 잠금 전용 표 — 행 하나가 논리 잠금 하나다(db/locks.py).

    [2026-09-05] 감사 체인·모델 활성 전환은 `pg_advisory_xact_lock` 으로만 잠겨 있어
    MariaDB 에서 조용히 꺼졌다. MariaDB 의 GET_LOCK 은 커넥션 단위라 PostgreSQL 의
    트랜잭션 단위 수명을 흉내 낼 수 없었다(실측 근거는 db/locks.py 머리말).
    `SELECT ... FOR UPDATE` 행 잠금은 두 dialect 모두 트랜잭션 단위라 호출부가 이미
    전제하던 수명(commit/rollback 에 자동 해제)과 정확히 맞는다.

    데이터를 담지 않는다 — 행의 존재 자체가 잠금 지점이다. 행은 처음 쓸 때 자동 생성된다.
    """

    __tablename__ = "tad_sy_lck_mng"

    name: Mapped[str] = mapped_column("lck_nm", String(64), primary_key=True)


class SampleDatasetMembership(Base):
    """승인 합성본이 어느 학습셋 판에 들어갔는지 — **append-only**.

    [2026-09-05] 앞선 판은 tad_sm_syn_doc_mng 에 칼럼 하나였는데 UPDATE 로 덮어써서
    **한 문서가 여러 판에 들어간 이력을 잃었다.** 재방출 한 번이면 앞선 기록이 사라진다.

    같은 표에서 생성 작업 연결도 푼다 — synth_job_id 가 어디에도 없어 "이 작업이 만든
    문서"를 물을 수 없었다(응답은 synth_job_id 를 주는데 그 뒤로 이어지는 곳이 없었다).

    지우거나 고치지 않는다. 쌓는다.
    """

    __tablename__ = "tad_sm_syn_datst_cpst_mng"

    membership_id: Mapped[int] = mapped_column("cpst_sn", BigInteger, primary_key=True, autoincrement=True)
    # nullable=False 는 동작을 바꾸지 않는다 — 마이그레이션이 이미 NOT NULL 로 만들었고 Mapped 표기도 같다.
    # 정의서 생성기가 칼럼 인자만 읽어 'NULL 허용'으로 적던 것을 바로잡으려고 명시했다(2026-09-11).
    sample_id: Mapped[uuid.UUID] = mapped_column(
        "syn_doc_id", Uuid(as_uuid=True),
        ForeignKey("tad_sm_syn_doc_mng.syn_doc_id", ondelete="CASCADE"),
        nullable=False,
    )
    dataset_version: Mapped[str] = mapped_column("datst_ver_nm", String(64), nullable=False)
    # 어느 생성 작업에서 나온 문서인가. 단발 호출·옛 행은 NULL.
    synth_job_id: Mapped[uuid.UUID | None] = mapped_column("syn_job_id", Uuid(as_uuid=True))
    created_at: Mapped[dt.datetime] = mapped_column(
        "crt_dt", DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        # 같은 판에 두 번 넣지 않는다. 재방출은 같은 내용이면 같은 판 이름이라 무해하게
        # 부딪히고, 내용이 바뀌면 새 판으로 한 줄 더 쌓인다.
        UniqueConstraint("syn_doc_id", "datst_ver_nm", name="uq_sdm_sample_version"),
        Index("idx_sdm_version", "datst_ver_nm"),
        Index("idx_sdm_job", "syn_job_id"),
    )


class Guide(Base):
    """가이드 문서 업로드 이력 — GuideService in-memory 대체."""
    __tablename__ = "tad_gm_guide_ver_mng"

    id: Mapped[int] = mapped_column("guide_ver_sn", BigInteger, Identity(always=True), primary_key=True)
    guide_id: Mapped[str] = mapped_column("guide_id", String(200), nullable=False)
    version: Mapped[str] = mapped_column("guide_ver_nm", String(50), nullable=False)
    effective_date: Mapped[str | None] = mapped_column("enfc_dt", String(30))
    change_summary: Mapped[str | None] = mapped_column("chg_smry_cn", Text)
    doc_type: Mapped[str | None] = mapped_column("doc_knd_nm", String(50))
    filename: Mapped[str | None] = mapped_column("file_nm", String(500))
    # nullable=False 는 동작을 바꾸지 않는다(마이그레이션이 NOT NULL · Mapped 표기도 같다) — 정의서 표기를 맞추려고 명시.
    registered_at: Mapped[dt.datetime] = mapped_column("reg_dt", DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        # 동시 업로드 시 중복 버전 행 방지(최종 방어선) — GuideRepo.upsert의
        # select-then-insert는 락이 없어 race에서 중복을 만들 수 있다.
        # tenant 제거: (guide_id, version) 전역 UNIQUE. 격리는 KL 포털 전담.
        UniqueConstraint("guide_id", "guide_ver_nm", name="uq_guides_id_version"),
        Index("idx_guides_guide_id", "guide_id"),
        Index("idx_guides_registered", "reg_dt"),
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
