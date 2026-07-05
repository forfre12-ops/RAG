"""POST /documents — 분류 대상 문서 업로드·ingestion.

협력사가 실제 업무 파일(HWP/PDF/DOCX 등)을 업로드하는 입구.
GuideService(/guide/documents)와 완전 분리 — 도메인이 다름:
  - /guide/documents : 영업비밀보호 가이드라인 → ES 지식베이스
  - /documents       : 등급 판정 대상 비밀문서 → documents/chunks/classifications
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from lloydk.api._jwt_auth import require_auth
from lloydk.config import settings
from lloydk.schemas.common import Actor
from lloydk.services.document_ingestion_service import (
    DocumentIngestionService,
    extraction_review_decision,
)

router = APIRouter(tags=["documents"], dependencies=[Depends(require_auth)])


def _get_ingestion_service() -> DocumentIngestionService:
    """Dependency — 테스트에서 app.dependency_overrides로 LocalStorage 주입."""
    return DocumentIngestionService()


class DocumentUploadResponse(BaseModel):
    doc_id: Optional[str]           # UUID str | None (DB 미가용 시 None)
    filename: str
    source_format: str
    file_hash: str
    file_size_bytes: int
    extraction_method: str
    extraction_quality: float
    ocr_used: bool
    char_count: int
    chunk_count: int
    persisted: bool
    warnings: list[str]
    # [P0#3] 저품질/OCR 추출 → 분류 전 검수 라우팅 신호. True면 processing_status='needs_review'로
    # 격리돼 자동분류를 그대로 통과하지 않는다(호출측이 검수 유도).
    requires_review: bool = False
    review_reasons: list[str] = []
    # index_for_rag=true 일 때만 채워짐 — 업로드 문서를 RAG 검색 대상으로 적재한 결과
    rag_indexed: bool = False
    rag_collection: Optional[str] = None
    rag_vector_count: int = 0


@router.post("/documents", response_model=DocumentUploadResponse, status_code=201)
async def upload_document(
    actor: str = Form(..., description="Actor JSON 문자열 (multipart 제약)"),
    doc_type: Optional[str] = Form(default=None),
    external_ref: Optional[str] = Form(default=None, description="외부 문서 ID (EDMS 등)"),
    index_for_rag: bool = Form(default=False, description="True면 업로드 문서를 RAG 검색 컬렉션에도 적재"),
    rag_namespace: Optional[str] = Form(default=None, description="RAG 적재 컬렉션(미지정 시 settings.rag_upload_collection)"),
    file: UploadFile = File(...),
    svc: DocumentIngestionService = Depends(_get_ingestion_service),
):
    """분류 대상 문서 업로드.

    파일을 받아 포맷 자동 감지(HWP/PDF/DOCX/TXT) → 텍스트 추출(필요 시 OCR) →
    원본 object storage 저장 → documents/chunks 적재.
    이후 POST /classify?doc_id=... 로 등급 판정 요청.
    """
    try:
        actor_obj = Actor.model_validate(json.loads(actor))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid actor json: {exc}") from exc

    max_bytes = settings.max_upload_mb * 1024 * 1024
    declared_size = getattr(file, "size", None)
    if declared_size is not None and declared_size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"file too large: {declared_size} > {max_bytes} bytes ({settings.max_upload_mb}MB)",
        )

    body = await file.read()
    if len(body) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"file too large: {len(body)} > {max_bytes} bytes ({settings.max_upload_mb}MB)",
        )
    if not body:
        raise HTTPException(status_code=422, detail="empty file")

    filename = file.filename or "unknown"
    # tenant 제거: 격리는 KL 포털 전담(상류 보장). 단일 고객사 엔진이라 ingest를 무스코프로 수행.
    result = svc.ingest(
        filename=filename,
        content_bytes=body,
        doc_type=doc_type,
        external_ref=external_ref,
        created_by=actor_obj.user_id,
    )

    rag_indexed = False
    rag_collection: Optional[str] = None
    rag_vector_count = 0
    if index_for_rag and result.persisted and result.doc_id is not None:
        rag_collection = (rag_namespace or "").strip() or getattr(
            settings, "rag_upload_collection", "uploads"
        )
        rag_indexed, rag_vector_count = _index_uploaded_doc(
            result.doc_id, rag_collection, result.warnings
        )

    return DocumentUploadResponse(
        doc_id=(str(result.doc_id) if result.doc_id is not None else None),
        filename=result.filename,
        source_format=result.source_format,
        file_hash=result.file_hash,
        file_size_bytes=result.file_size_bytes,
        extraction_method=result.extraction_method,
        extraction_quality=result.extraction_quality,
        ocr_used=result.ocr_used,
        char_count=result.char_count,
        chunk_count=result.chunk_count,
        persisted=result.persisted,
        warnings=result.warnings,
        requires_review=result.requires_review,
        review_reasons=result.review_reasons,
        rag_indexed=rag_indexed,
        rag_collection=rag_collection if rag_indexed else None,
        rag_vector_count=rag_vector_count,
    )


def _index_uploaded_doc(
    doc_id, collection: str, warnings: list[str]
) -> tuple[bool, int]:
    """업로드된 문서의 DB chunks를 RAG 검색 컬렉션에 적재. 실패는 warnings에 누적.

    tenant 제거: 격리는 KL 포털 전담 → 무스코프 조회·적재.
    """
    try:
        from lloydk.db import SessionLocal  # noqa: PLC0415
        from lloydk.rag.document_indexer import index_document_for_rag  # noqa: PLC0415
        from lloydk.repositories.chunk_repo import ChunkRepo  # noqa: PLC0415

        db = SessionLocal()
        try:
            rows = ChunkRepo(db).get_by_doc_id(doc_id)
            chunks = [(c.chunk_index, c.content) for c in rows]
        finally:
            db.close()

        res = index_document_for_rag(
            doc_id=str(doc_id), collection=collection, chunks=chunks
        )
        warnings.extend(f"rag-index: {w}" for w in res.warnings)
        return res.indexed, res.vector_count
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"rag-index failed: {type(exc).__name__}: {exc}")
        return False, 0


# ---------------------------------------------------------------------------
# POST /documents/analyze — 업로드 1회로 파싱→검수게이트→분류 전 구간을 단계별로 반환.
# 시연/관리자 콘솔이 "이 문서가 어떻게 파싱됐고, 어떤 게이트를 거쳐, 어떤 등급이 됐는지"를
# 한 화면에 보여주기 위한 백본. DB/스토리지 없이 in-process로 동작(persist=False, content 분류).
# 운영 적재 경로(POST /documents → /classify?doc_id)와 별개의 read-only 진단 엔드포인트.
# ---------------------------------------------------------------------------
class AnalyzeStage(BaseModel):
    name: str          # 업로드 | 추출 | 정규화·PII마스킹 | 청킹 | 검수게이트 | 분류 | 결과
    status: str        # done | review | skipped | fail
    detail: str


class AnalyzeParseInfo(BaseModel):
    source_format: str
    extraction_method: str
    extraction_quality: float
    content_quality: float
    ocr_used: bool
    char_count: int
    chunk_count: int
    table_coverage: Optional[str] = None
    table_count: int = 0
    table_cell_count: int = 0
    warnings: list[str] = Field(default_factory=list)
    pii_masked_count: int = 0
    extract_error: Optional[str] = None


class AnalyzeGateInfo(BaseModel):
    requires_review: bool
    reasons: list[str]


class AnalyzeClassification(BaseModel):
    label: str
    confidence: float
    scores: dict
    status: str                     # staging(자동확정) | needs_review(게이트)
    model_version: str
    factors: dict                   # 평가요소 S/V/M (+ scores)
    factors_source: Optional[str] = None
    warnings: list[str] = []
    elapsed_ms: int = 0


class DocumentAnalysisResponse(BaseModel):
    filename: str
    file_size_bytes: int
    parse: AnalyzeParseInfo
    gate: AnalyzeGateInfo
    classification: Optional[AnalyzeClassification] = None
    evidence: list[dict] = []
    text_preview: str = ""
    stages: list[AnalyzeStage] = []


@router.post("/documents/analyze", response_model=DocumentAnalysisResponse)
async def analyze_document(
    file: UploadFile = File(...),
    return_evidence: bool = Form(default=True),
):
    """업로드 문서를 파싱→검수게이트→분류까지 한 번에 돌려 단계별 진단 결과를 반환.

    자동 적재(documents/chunks) 없이 read-only. 최종 등급은 추출 텍스트(content) 기반으로
    실제 서빙 경로(ClassifyService)와 동일 게이트를 통과해 산출된다.
    """
    from lloydk.modules.m2_preprocess.pipeline import PreprocessPipeline  # noqa: PLC0415
    from lloydk.schemas.classify import ClassifyRequest  # noqa: PLC0415
    from lloydk.services.classify_service import ClassifyService  # noqa: PLC0415

    max_bytes = settings.max_upload_mb * 1024 * 1024
    body = await file.read()
    if not body:
        raise HTTPException(status_code=422, detail="empty file")
    if len(body) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"file too large: {len(body)} > {max_bytes} bytes ({settings.max_upload_mb}MB)",
        )

    filename = file.filename or "unknown"
    source_format = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    stages: list[AnalyzeStage] = [
        AnalyzeStage(name="업로드", status="done", detail=f"{filename} ({len(body):,} bytes)")
    ]

    # --- 파싱(추출→정규화·PII→청킹) ---
    fd, tmp_path = tempfile.mkstemp(suffix=f".{source_format}" if source_format else "")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
        try:
            pre = PreprocessPipeline().run_file(tmp_path)
        except Exception as exc:  # noqa: BLE001
            stages.append(AnalyzeStage(name="추출", status="fail", detail=f"{type(exc).__name__}: {exc}"))
            raise HTTPException(status_code=422, detail=f"parse failed: {exc}") from exc
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    ex = pre.extraction
    pii_count = sum(pre.pii_counts.values()) if pre.pii_counts else 0
    extracted_tables = list(getattr(ex, "tables", []) or [])
    extraction_warnings = list(getattr(ex, "warnings", []) or [])
    table_cell_count = sum(
        len(row)
        for table in extracted_tables
        for row in getattr(table, "rows", []) or []
    )
    parse = AnalyzeParseInfo(
        source_format=source_format or ex.method,
        extraction_method=ex.method,
        extraction_quality=round(ex.quality, 3),
        content_quality=round(pre.quality, 3),
        ocr_used=ex.ocr_used,
        char_count=len(pre.text),
        chunk_count=len(pre.chunks),
        table_coverage=getattr(ex, "table_coverage", None),
        table_count=len(extracted_tables),
        table_cell_count=table_cell_count,
        warnings=extraction_warnings,
        pii_masked_count=pii_count,
        extract_error=ex.error or None,
    )
    stages.append(AnalyzeStage(
        name="추출", status="fail" if ex.error else "done",
        detail=f"{ex.method} · 품질 {ex.quality:.2f} · {len(pre.text):,}자"
        + (" · OCR" if ex.ocr_used else "") + (f" · {ex.error}" if ex.error else ""),
    ))
    stages.append(AnalyzeStage(
        name="정규화·PII마스킹", status="done",
        detail=f"콘텐츠품질 {pre.quality:.2f} · 개인정보 {pii_count}건 마스킹",
    ))
    stages.append(AnalyzeStage(
        name="청킹", status="done", detail=f"{len(pre.chunks)}개 청크",
    ))

    # --- 검수 게이트(운영 ingest와 동일 함수) ---
    dec = extraction_review_decision(
        quality=ex.quality,
        ocr_used=ex.ocr_used,
        error=ex.error,
        min_quality=float(getattr(settings, "extraction_review_min_quality", 0.6)),
        ocr_requires_review=bool(getattr(settings, "extraction_ocr_requires_review", True)),
        content_quality=pre.quality,
        table_coverage=getattr(ex, "table_coverage", None),
    )
    gate = AnalyzeGateInfo(requires_review=dec.requires_review, reasons=list(dec.reasons))
    stages.append(AnalyzeStage(
        name="검수게이트",
        status="review" if dec.requires_review else "done",
        detail="검수 라우팅: " + ", ".join(dec.reasons) if dec.requires_review else "자동 경로 통과(무오탐)",
    ))

    resp = DocumentAnalysisResponse(
        filename=filename,
        file_size_bytes=len(body),
        parse=parse,
        gate=gate,
        text_preview=pre.text[:600],
        stages=stages,
    )

    # NOTE: Pydantic이 생성 시 stages 리스트를 복사하므로, 이후 단계는 resp.stages에 직접 append.
    # 빈 본문은 분류 불가 — failed 경로
    if len(pre.text.strip()) == 0:
        resp.stages.append(AnalyzeStage(name="분류", status="skipped", detail="본문 추출 0자 — 분류 불가(failed)"))
        resp.stages.append(AnalyzeStage(name="결과", status="review", detail="본문 없음 → 검수 필요"))
        return resp

    # --- 분류(content 기반, 실제 서빙 게이트 통과) ---
    t0 = time.perf_counter()
    try:
        cls = ClassifyService().classify(
            ClassifyRequest(doc_id=filename, content=pre.text, return_evidence=return_evidence)
        )
    except Exception as exc:  # noqa: BLE001
        resp.stages.append(AnalyzeStage(name="분류", status="fail", detail=f"{type(exc).__name__}: {exc}"))
        return resp
    elapsed = int((time.perf_counter() - t0) * 1000)
    label = cls.label.value if hasattr(cls.label, "value") else str(cls.label)
    factors = cls.evaluation_factors.model_dump() if getattr(cls, "evaluation_factors", None) else {}
    resp.classification = AnalyzeClassification(
        label=label,
        confidence=round(cls.confidence, 3),
        scores={k: round(float(v), 3) for k, v in (cls.scores or {}).items()},
        status=cls.status,
        model_version=cls.model_version,
        factors=factors,
        factors_source=getattr(cls, "factors_source", None),
        warnings=list(cls.warnings or []),
        elapsed_ms=cls.elapsed_ms or elapsed,
    )
    if return_evidence and getattr(cls, "evidence", None):
        resp.evidence = [
            e.model_dump() if hasattr(e, "model_dump") else dict(e) for e in cls.evidence
        ]
    resp.stages.append(AnalyzeStage(
        name="분류", status="done",
        detail=f"{label} · 신뢰도 {cls.confidence:.2f} · {cls.model_version}",
    ))
    resp.stages.append(AnalyzeStage(
        name="결과",
        status="review" if cls.status == "needs_review" else "done",
        detail="검수 필요(needs_review)" if cls.status == "needs_review" else "자동 확정(staging)",
    ))
    return resp
