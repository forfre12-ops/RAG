"""POST /documents — 분류 대상 문서 업로드·ingestion.

협력사가 실제 업무 파일(HWP/PDF/DOCX 등)을 업로드하는 입구.
GuideService(/guide/documents)와 완전 분리 — 도메인이 다름:
  - /guide/documents : 영업비밀보호 가이드라인 → ES 지식베이스
  - /documents       : 등급 판정 대상 비밀문서 → documents/chunks/classifications
"""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from lloydk.api._jwt_auth import require_auth
from lloydk.config import settings
from lloydk.schemas.common import Actor
from lloydk.services.document_ingestion_service import DocumentIngestionService

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
