"""POST /documents — 분류 대상 문서 업로드·ingestion.

협력사가 실제 업무 파일(HWP/PDF/DOCX 등)을 업로드하는 입구.
GuideService(/guide/documents)와 완전 분리 — 도메인이 다름:
  - /guide/documents : 영업비밀보호 가이드라인 → ES 지식베이스
  - /documents       : 등급 판정 대상 비밀문서 → documents/chunks/classifications
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field

from koipa.api._jwt_auth import require_auth
from koipa.api.confirm import bind_authenticated_actor
from koipa.config import settings
from koipa.schemas.common import Actor
from koipa.services.document_ingestion_service import (
    DocumentIngestionService,
    extraction_review_decision,
)

router = APIRouter(tags=["documents"], dependencies=[Depends(require_auth)])

# [#14] 업로드 RAG 적재 대상(collection)은 caller 지정이라, 검증 없이 두면 인증 사용자가 시스템/
# 평가/가이드 코퍼스(docs·legal·secrets-guides·gold 등)에 임의 문서를 심어 검색·평가 결과를
# 오염(corpus poisoning)시킬 수 있다. pgvector 파라미터 바인딩이라 SQLi 는 아니나, 네임스페이스를
# 안전 charset(소문자 강제)으로 제한하고 보호 컬렉션을 거부한다. 미지정 시 기본 업로드 컬렉션 사용.
_RAG_NS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_RAG_NS_PROTECTED = frozenset({
    "docs", "legal", "secrets-guides", "gold", "gold_real", "golden",
    "eval", "locked_gold_eval", "retrieval_gold",
})


def _validate_rag_namespace(ns: Optional[str]) -> None:
    """caller 지정 rag_namespace 를 안전 charset + 보호 컬렉션 거부로 검증(빈값=기본 사용, 통과)."""
    if ns is None:
        return
    v = ns.strip()
    if not v:
        return
    if not _RAG_NS_RE.match(v) or v in _RAG_NS_PROTECTED:
        raise HTTPException(
            status_code=422,
            detail=f"invalid rag_namespace: {ns!r} (허용: ^[a-z0-9][a-z0-9_-]{{0,63}}$, "
                   "시스템/평가 컬렉션 불가)",
        )


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
    pages_processed: Optional[int] = None
    pages_total: Optional[int] = None
    extraction_complete: bool = True
    classification_job_id: Optional[str] = None
    classification_status: Optional[str] = None
    classification_status_url: Optional[str] = None
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
    # [ICD §3.1] 서비스는 source_type 을 받아 metadata_ 에 저장하고 분류 때 Gate-1(출처
    # prior)이 그것을 읽는다. 그런데 이 HTTP 경로에 파라미터가 없어서 **업로드로 들어온
    # 문서는 출처를 영영 줄 수 없었다** — 서비스 인자가 코드 안에서만 도달 가능했다.
    # 실측 2026-08-14: 그 결과 공개문서를 업로드해도 출처 cap 이 걸릴 자리가 없다.
    source_type: Optional[str] = Form(
        default=None,
        description="ICD §3.1 출처: public | registered_patent | academic | internal | "
                    "external_confidential. 공개 출처면 분류 시 S3 로 cap 된다.",
    ),
    # [ICD §3.2·§3.3] 관리성(M)의 근거. **본문에서 관측되지 않는 축**이라 이 경로가
    # 없으면 영영 못 받는다 — 실측 2026-08-15: 실문서 업무문서에 관리 표시가 있는 것은
    # 17~21% 뿐이고 나머지는 unknown 으로 남아 보수적 완성이 고등급으로 올린다.
    # 그리고 정본에서 S1 은 (2,2,0) 하나뿐이라 M 이 0 으로 확정되지 않으면 **S1 이
    # 구조적으로 도달 불가**다(봉인 판정면의 28%가 그래서 전부 TS 가 됐다).
    #
    # /classify 는 metadata dict 로 이미 받는데 업로드 경로에는 자리가 없었다.
    # 파일로 들어온 문서는 관리성을 줄 방법이 없어 그 갭이 그대로 남는다.
    security_marking: Optional[str] = Form(
        default=None,
        description="ICD §3.2 보안표시: top_secret | secret | confidential | none. "
                    "표기가 있으면 접근범위보다 우선한다.",
    ),
    access_scope: Optional[str] = Form(
        default=None,
        description="ICD §3.3 접근범위: approved_only | designated | department | "
                    "all_employees. 보안표시가 none 일 때 관리수준(M)을 정하는 주 입력.",
    ),
    index_for_rag: bool = Form(default=False, description="True면 업로드 문서를 RAG 검색 컬렉션에도 적재"),
    rag_namespace: Optional[str] = Form(default=None, description="RAG 적재 컬렉션(미지정 시 settings.rag_upload_collection)"),
    enqueue_classification: bool = Form(default=False),
    file: UploadFile = File(...),
    svc: DocumentIngestionService = Depends(_get_ingestion_service),
    auth: dict = Depends(require_auth),
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
    # [#13] created_by 감사 신원을 인증 principal 로 확정(body 자칭 위조 차단; JWT sub 우선).
    bind_authenticated_actor(actor_obj, auth)
    # [#14] RAG 적재 네임스페이스 검증 — 시스템/평가 컬렉션 오염 차단(파일 읽기 전 fail-fast).
    _validate_rag_namespace(rag_namespace)

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
        source_type=source_type,
        security_marking=security_marking,
        access_scope=access_scope,
        external_ref=external_ref,
        created_by=actor_obj.user_id,
    )

    classification_job_id: Optional[str] = None
    classification_status: Optional[str] = None
    classification_status_url: Optional[str] = None
    if enqueue_classification:
        if not result.persisted or result.doc_id is None or result.char_count == 0:
            result.warnings.append(
                "classification was not queued: document was not persisted or has no extracted text"
            )
        else:
            try:
                from koipa.schemas.classify_async import ClassifyAsyncRequest  # noqa: PLC0415
                from koipa.services.async_classify_service import AsyncClassifyService  # noqa: PLC0415

                job = AsyncClassifyService().submit_async(
                    ClassifyAsyncRequest(doc_id=str(result.doc_id))
                )
                classification_job_id = str(job.job_id)
                classification_status = job.status
                classification_status_url = job.status_url
            except Exception as exc:  # noqa: BLE001
                result.warnings.append(
                    f"classification was not queued: {type(exc).__name__}"
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
        pages_processed=result.pages_processed,
        pages_total=result.pages_total,
        extraction_complete=result.extraction_complete,
        classification_job_id=classification_job_id,
        classification_status=classification_status,
        classification_status_url=classification_status_url,
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
        from koipa.db import SessionLocal  # noqa: PLC0415
        from koipa.rag.document_indexer import index_document_for_rag  # noqa: PLC0415
        from koipa.repositories.chunk_repo import ChunkRepo  # noqa: PLC0415

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
    ms: Optional[int] = None  # 단계 소요(ms) — 실측 가능한 단계만(파싱·분류). 표시 전용.


class AnalyzeParseInfo(BaseModel):
    source_format: str
    extraction_method: str
    extraction_quality: float
    content_quality: float
    ocr_used: bool
    char_count: int
    chunk_count: int
    pages_processed: Optional[int] = None
    pages_total: Optional[int] = None
    extraction_complete: bool = True
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
    # 역산이 있었을 때만 채워진다 — 화면이 '룰 관측' 과 '모델 역산' 을 나란히 보이게 한다.
    rule_factors: Optional[dict] = None
    warnings: list[str] = []
    elapsed_ms: int = 0
    # [투명성/시연] 하이브리드 각 엔진 원시 판정 + 결합경로
    rule_grade: Optional[str] = None
    model_grade: Optional[str] = None
    decision_path: Optional[str] = None


class DocumentAnalysisResponse(BaseModel):
    filename: str
    file_size_bytes: int
    parse: AnalyzeParseInfo
    gate: AnalyzeGateInfo
    classification: Optional[AnalyzeClassification] = None
    evidence: list[dict] = []
    text_preview: str = ""
    # [골든] 절단 없는 전체 추출 본문 — full_text=true 일 때만 채운다. 골든 후보 텍스트로
    # text_preview(8K 절단)를 쓰면 라벨은 전문 기준인데 저장 본문만 잘려 후보가 오염된다.
    text: Optional[str] = None
    stages: list[AnalyzeStage] = []


@router.post("/documents/analyze", response_model=DocumentAnalysisResponse)
async def analyze_document(
    file: UploadFile = File(...),
    return_evidence: bool = Form(default=True),
    full_text: bool = Form(default=False),
    # [ICD §3.1~§3.3] 시연·연동이 실제로 타는 경로가 여기다. 종전에는 메타데이터 자리가
    # 아예 없어 **콘솔로 올린 문서는 출처도 관리성도 줄 수 없었다** — /classify 는 dict
    # 로 받는데 이쪽만 빠져 있었다. 관리성은 본문에서 관측되지 않는 축이므로(실측
    # 2026-08-15: 실문서 업무문서 중 관리 표시 보유 17~21%) 여기서 못 받으면 unknown 이
    # 남고, 정본에서 S1 은 (2,2,0) 하나뿐이라 **S1 이 구조적으로 도달 불가**가 된다.
    source_type: Optional[str] = Form(
        default=None,
        description="ICD §3.1 출처: public | registered_patent | academic | internal | "
                    "external_confidential",
    ),
    security_marking: Optional[str] = Form(
        default=None, description="ICD §3.2 보안표시: top_secret | secret | confidential | none",
    ),
    access_scope: Optional[str] = Form(
        default=None,
        description="ICD §3.3 접근범위: approved_only | designated | department | all_employees",
    ),
):
    """업로드 문서를 파싱→검수게이트→분류까지 한 번에 돌려 단계별 진단 결과를 반환.

    자동 적재(documents/chunks) 없이 read-only. 최종 등급은 추출 텍스트(content) 기반으로
    실제 서빙 경로(ClassifyService)와 동일 게이트를 통과해 산출된다.
    """
    from koipa.modules.m2_preprocess.pipeline import PreprocessPipeline  # noqa: PLC0415
    from koipa.schemas.classify import ClassifyRequest  # noqa: PLC0415
    from koipa.services.classify_service import ClassifyService  # noqa: PLC0415

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
        _t_parse = time.perf_counter()
        try:
            pre = PreprocessPipeline().run_file(tmp_path)
        except Exception as exc:  # noqa: BLE001
            stages.append(AnalyzeStage(name="추출", status="fail", detail=f"{type(exc).__name__}: {exc}"))
            raise HTTPException(status_code=422, detail=f"parse failed: {exc}") from exc
        _parse_ms = int((time.perf_counter() - _t_parse) * 1000)
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
        pages_processed=getattr(ex, "pages", None),
        pages_total=(getattr(ex, "total_pages", None) or getattr(ex, "pages", None)),
        extraction_complete=(
            not any("truncated" in str(w).lower() for w in extraction_warnings)
            and not (
                getattr(ex, "pages", None) is not None
                and getattr(ex, "total_pages", None) is not None
                and getattr(ex, "pages") < getattr(ex, "total_pages")
            )
        ),
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
        ms=_parse_ms,
    ))

    # [용량 게이트] 분류는 청크 수에 비례한다. 상한을 넘으면 여기서 거절한다 —
    # 그냥 진행하면 gunicorn --timeout 이 워커를 죽이고 클라이언트는 아무 설명 없이
    # 빈 응답을 받으며, --workers 1 이면 동시에 처리 중이던 다른 요청도 함께 끊긴다.
    # 조용히 죽는 것보다 즉시 이유를 말하고 거절하는 편이 낫다. 추출 결과는 이미
    # 나왔으므로 무엇이 얼마나 컸는지 숫자로 알려준다(문제 진단이 가능해야 한다).
    _chunk_cap = int(getattr(settings, "analyze_sync_max_chunks", 0) or 0)
    if _chunk_cap > 0 and len(pre.chunks) > _chunk_cap:
        raise HTTPException(
            status_code=413,
            detail=(
                f"document too large for synchronous analysis: "
                f"{len(pre.chunks)} chunks > {_chunk_cap} "
                f"({len(pre.text):,} chars, {len(extracted_tables)} tables). "
                f"이 엔드포인트는 진단용 동기 경로라 요청 안에서 분류까지 끝낸다 — "
                f"청크가 많으면 워커 타임아웃으로 무응답이 된다. "
                f"대용량 문서는 적재 후 비동기 분류(POST /documents → POST /classify/async)를 쓰거나, "
                f"배포 처리량을 재고(scripts/probe_ingest_capacity.py) "
                f"KOIPA_ANALYZE_SYNC_MAX_CHUNKS 를 조정할 것."
            ),
        )
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
        warnings=(getattr(ex, "warnings", None)
                  if bool(getattr(settings, "extraction_content_loss_review", True)) else None),
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
        # 본문 미리보기 — 시연에서 본문 확인용으로 넉넉히(8K자). 분류는 전체 본문으로 수행되며
        # 이 값은 표시 전용. 매우 긴 문서만 말미 절단(응답 비대화 방지).
        text_preview=pre.text[:8000],
        # 골든 후보 적재처럼 무손실 본문이 필요한 호출만 opt-in(기본 미포함 — 응답 비대화 방지).
        text=pre.text if full_text else None,
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
        # 폼에 실린 ICD 필드만 metadata 로 넘긴다 — 빈 값을 넣으면 "unknown" 과
        # "명시적으로 없음" 이 구분되지 않아 관리성 판정이 뒤집힌다.
        _icd = {k: v for k, v in (
            ("source_type", source_type),
            ("security_marking", security_marking),
            ("access_scope", access_scope),
        ) if v}
        cls = ClassifyService().classify(
            ClassifyRequest(doc_id=filename, content=pre.text,
                            metadata=_icd or None, return_evidence=return_evidence)
        )
    except Exception as exc:  # noqa: BLE001
        resp.stages.append(AnalyzeStage(name="분류", status="fail", detail=f"{type(exc).__name__}: {exc}"))
        return resp
    elapsed = int((time.perf_counter() - t0) * 1000)
    label = cls.label.value if hasattr(cls.label, "value") else str(cls.label)
    factors = cls.evaluation_factors.model_dump() if getattr(cls, "evaluation_factors", None) else {}
    # [2026-08-20] 룰이 실제로 관측한 S/V/M. factors 가 모델 등급으로 역산된 경우에만 채워진다.
    rule_factors = (
        cls.rule_evaluation_factors.model_dump()
        if getattr(cls, "rule_evaluation_factors", None) else None
    )
    # 추출 검수게이트(표누락/OCR/저품질/추출오류)를 최종 status 로 조정 — content 경로 분류는
    # review_flagged 를 안 태우므로, 게이트가 검수를 요구하면 여기서 needs_review 로 승격해
    # box4('검수 필요')와 box5(최종 status)가 모순되지 않게 하고 실 doc_id 서빙과 일치시킨다.
    cls_warnings = list(cls.warnings or [])
    eff_status = cls.status
    if dec.requires_review and cls.status != "needs_review":
        eff_status = "needs_review"
        cls_warnings.append(
            "extraction_gate: 열화 추출(표누락/OCR/저품질)→검수 라우팅 (" + ", ".join(dec.reasons) + ")"
        )
    resp.classification = AnalyzeClassification(
        label=label,
        confidence=round(cls.confidence, 3),
        scores={k: round(float(v), 3) for k, v in (cls.scores or {}).items()},
        status=eff_status,
        model_version=cls.model_version,
        factors=factors,
        factors_source=getattr(cls, "factors_source", None),
        rule_factors=rule_factors,
        warnings=cls_warnings,
        elapsed_ms=cls.elapsed_ms or elapsed,
        rule_grade=getattr(cls, "rule_grade", None),
        model_grade=getattr(cls, "model_grade", None),
        decision_path=getattr(cls, "decision_path", None),
    )
    if return_evidence and getattr(cls, "evidence", None):
        resp.evidence = [
            e.model_dump() if hasattr(e, "model_dump") else dict(e) for e in cls.evidence
        ]
    resp.stages.append(AnalyzeStage(
        name="분류", status="done",
        detail=f"{label} · 신뢰도 {cls.confidence:.2f} · {cls.model_version}",
        ms=cls.elapsed_ms or elapsed,
    ))
    resp.stages.append(AnalyzeStage(
        name="결과",
        status="review" if eff_status == "needs_review" else "done",
        detail="검수 필요(needs_review)" if eff_status == "needs_review" else "자동 확정(staging)",
    ))
    return resp
