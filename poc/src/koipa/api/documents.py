"""POST /documents — 분류 대상 문서 업로드·ingestion.

협력사가 실제 업무 파일(HWP/PDF/DOCX 등)을 업로드하는 입구.
등급 판정 대상 비밀문서 → documents/chunks/classifications
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
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

def _get_ingestion_service() -> DocumentIngestionService:
    """Dependency — 테스트에서 app.dependency_overrides로 LocalStorage 주입."""
    return DocumentIngestionService()


def _read_shared_mount_file(raw_path: str) -> tuple[str, bytes]:
    """[2026-10-06] KL 요청 — 업로드 대신 "이미 같은 VM에 있는 파일"을 경로로 등록.

    documents_shared_mount_dir 가 비어 있으면(기본) 기능 자체를 끈다. 설정돼 있어도
    받은 경로가 그 디렉터리 밖을 가리키면 거절한다(경로조작 방어) — "같은 VM"이라는
    전제를 받은 문자열 그대로 신뢰하지 않고, 실제로 컨테이너에 마운트된 그 폴더
    안에 있는지 매번 다시 확인한다. 반환: (파일명, 바이트).
    """
    base = (getattr(settings, "documents_shared_mount_dir", "") or "").strip()
    if not base:
        raise HTTPException(
            status_code=422,
            detail="file_path is disabled on this deployment (documents_shared_mount_dir unset)",
        )
    base_resolved = Path(base).resolve(strict=False)
    try:
        candidate = Path(raw_path).resolve(strict=False)
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid file_path: {exc}") from exc
    try:
        candidate.relative_to(base_resolved)
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail="file_path must be inside the shared directory configured on this deployment",
        )
    if not candidate.is_file():
        raise HTTPException(status_code=422, detail="file_path does not exist or is not a file")
    try:
        body = candidate.read_bytes()
    except OSError as exc:
        raise HTTPException(status_code=422, detail=f"file_path could not be read: {exc}") from exc
    return candidate.name, body


class DocumentUploadResponse(BaseModel):
    doc_id: Optional[str]           # UUID str | None (DB 미가용 시 None)
    filename: str
    source_format: str
    file_hash: str
    file_size_bytes: int
    extraction_method: str
    extraction_quality: float
    char_count: int
    chunk_count: int
    pages_total: Optional[int] = None
    # [2026-10-06] 호출자가 보낸 값을 그대로 돌려준다(doc_id dedupe·job_id 선후관계 때문에
    # 둘 다 요청↔결과를 미리 묶는 키가 못 된다 — classify_async.py 참고). 서버는 저장·반환만.
    client_request_id: Optional[str] = None
    classification_job_id: Optional[str] = None
    classification_status: Optional[str] = None
    classification_status_url: Optional[str] = None
    persisted: bool
    warnings: list[str]
    # [P0#3] 저품질 추출 → 분류 전 검수 라우팅 신호. True면 processing_status='needs_review'로
    # 격리돼 자동분류를 그대로 통과하지 않는다(호출측이 검수 유도).
    requires_review: bool = False
    review_reasons: list[str] = []


def _ingest_to_response(
    svc: DocumentIngestionService,
    *,
    filename: str,
    content_bytes: bytes,
    source_type: Optional[str],
    security_marking: Optional[str],
    access_scope: Optional[str],
    created_by: str,
    client_request_id: Optional[str],
) -> DocumentUploadResponse:
    """파일 1건 적재 → 분류 관련 필드는 비운 DocumentUploadResponse. 단건·배치 공용."""
    result = svc.ingest(
        filename=filename,
        content_bytes=content_bytes,
        source_type=source_type,
        security_marking=security_marking,
        access_scope=access_scope,
        created_by=created_by,
    )
    return DocumentUploadResponse(
        doc_id=(str(result.doc_id) if result.doc_id is not None else None),
        filename=result.filename,
        source_format=result.source_format,
        file_hash=result.file_hash,
        file_size_bytes=result.file_size_bytes,
        extraction_method=result.extraction_method,
        extraction_quality=result.extraction_quality,
        char_count=result.char_count,
        chunk_count=result.chunk_count,
        pages_total=result.pages_total,
        client_request_id=client_request_id,
        persisted=result.persisted,
        warnings=result.warnings,
        requires_review=result.requires_review,
        review_reasons=result.review_reasons,
    )


@router.post("/documents", response_model=DocumentUploadResponse, status_code=201)
async def upload_document(
    actor: str = Form(..., description="Actor JSON 문자열 (multipart 제약)"),
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
    enqueue_classification: bool = Form(default=False),
    # [2026-10-06] KL 요청 — 등록+분류(enqueue_classification=true)의 완료 통보를 받을 주소.
    # ClassifyAsyncRequest 는 이미 이 필드가 있었지만 이 엔드포인트엔 받을 자리가 없어 내부
    # submit_async 호출에 전달되지 않았다(구조적으로 끊김) — 그 결과 "콜백을 못 받는다"였다.
    callback_url: Optional[str] = Form(
        default=None,
        description="enqueue_classification=true 일 때 분류 완료·실패 결과를 받을 webhook 주소(선택).",
    ),
    # [2026-10-06] KL 요청 — doc_id dedupe·job_id 선후관계 때문에 둘 다 호출자 쪽 매칭 키가
    # 못 된다. 그대로 저장해 응답·작업 조회·콜백에 돌려준다(서버는 해석하지 않음).
    client_request_id: Optional[str] = Form(default=None),
    # [2026-10-06] KL 요청 — 파일을 업로드하지 않고, 같은 VM에 이미 있는 파일의 경로로
    # 등록. file 과 file_path 중 정확히 하나만 보낸다. documents_shared_mount_dir 가
    # 설정돼 있어야 하고, 그 디렉터리 밖을 가리키면 거절한다(_read_shared_mount_file).
    file_path: Optional[str] = Form(
        default=None,
        description="file 대신 서버가 읽을 절대경로(선택). documents_shared_mount_dir 로 "
                    "지정된 공유 디렉터리 안에 있어야 한다 — 이 설정이 없으면 422.",
    ),
    file: Optional[UploadFile] = File(default=None),
    svc: DocumentIngestionService = Depends(_get_ingestion_service),
    auth: dict = Depends(require_auth),
):
    """분류 대상 문서 업로드.

    파일을 받아 포맷 자동 감지(HWP/PDF/DOCX/TXT) → 텍스트 추출 →
    원본 object storage 저장 → documents/chunks 적재.
    이후 POST /classify 본문에 {"doc_id": ...} 를 실어 등급 판정 요청
    (/classify 는 쿼리 파라미터를 받지 않는다). enqueue_classification=True 면 업로드와
    동시에 비동기 분류를 큐에 건다.
    """
    try:
        actor_obj = Actor.model_validate(json.loads(actor))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid actor json: {exc}") from exc
    # [#13] created_by 감사 신원을 인증 principal 로 확정(body 자칭 위조 차단; JWT sub 우선).
    bind_authenticated_actor(actor_obj, auth)
    max_bytes = settings.max_upload_mb * 1024 * 1024

    if file is not None and file_path:
        raise HTTPException(status_code=422, detail="give either file or file_path, not both")
    if file is None and not file_path:
        raise HTTPException(status_code=422, detail="file or file_path is required")

    if file_path:
        filename, body = _read_shared_mount_file(file_path)
        if len(body) > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"file too large: {len(body)} > {max_bytes} bytes ({settings.max_upload_mb}MB)",
            )
        if not body:
            raise HTTPException(status_code=422, detail="empty file")
    else:
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
    resp = _ingest_to_response(
        svc,
        filename=filename,
        content_bytes=body,
        source_type=source_type,
        security_marking=security_marking,
        access_scope=access_scope,
        created_by=actor_obj.user_id,
        client_request_id=client_request_id,
    )

    if enqueue_classification:
        if not resp.persisted or resp.doc_id is None or resp.char_count == 0:
            resp.warnings.append(
                "classification was not queued: document was not persisted or has no extracted text"
            )
        else:
            try:
                from koipa.schemas.classify_async import ClassifyAsyncRequest  # noqa: PLC0415
                from koipa.services.async_classify_service import AsyncClassifyService  # noqa: PLC0415

                job = AsyncClassifyService().submit_async(
                    ClassifyAsyncRequest(
                        doc_id=resp.doc_id, callback_url=callback_url,
                        client_request_id=client_request_id,
                    )
                )
                resp.classification_job_id = str(job.job_id)
                resp.classification_status = job.status
                resp.classification_status_url = job.status_url
            except Exception as exc:  # noqa: BLE001
                resp.warnings.append(
                    f"classification was not queued: {type(exc).__name__}"
                )

    return resp


def _batch_item_error(
    filename: str, client_request_id: Optional[str], message: str, *, file_size_bytes: int = 0,
) -> DocumentUploadResponse:
    """ingest() 호출 전 단계(크기·공백 파일)에서 걸린 배치 항목 — 단건 경로의 "지원하지
    않는 형식도 201"과 같은 언어로, 실패를 예외로 던지지 않고 그 파일의 결과 자리에 채운다.
    """
    return DocumentUploadResponse(
        doc_id=None, filename=filename, source_format="", file_hash="",
        file_size_bytes=file_size_bytes, extraction_method="none", extraction_quality=0.0,
        char_count=0, chunk_count=0, client_request_id=client_request_id,
        persisted=False, warnings=[message],
    )


class DocumentBatchUploadResponse(BaseModel):
    total: int
    results: list[DocumentUploadResponse]
    classification_job_id: Optional[str] = None
    classification_status: Optional[str] = None
    classification_status_url: Optional[str] = None


@router.post("/documents/batch", response_model=DocumentBatchUploadResponse, status_code=201)
async def upload_documents_batch(
    actor: str = Form(..., description="Actor JSON 문자열 (multipart 제약)"),
    source_type: Optional[str] = Form(default=None, description="ICD §3.1 — 파일 전체에 공통 적용"),
    security_marking: Optional[str] = Form(default=None, description="ICD §3.2 — 파일 전체에 공통 적용"),
    access_scope: Optional[str] = Form(default=None, description="ICD §3.3 — 파일 전체에 공통 적용"),
    enqueue_classification: bool = Form(default=False),
    callback_url: Optional[str] = Form(
        default=None,
        description="enqueue_classification=true 일 때, 이 배치의 분류가 모두 끝나면(부분 실패 포함) 한 번 통보받을 webhook 주소(선택).",
    ),
    # 파일 순서와 1:1 매칭(생략 가능) — 길이가 files 와 다르면 422.
    client_request_ids: Optional[list[str]] = Form(default=None),
    files: list[UploadFile] = File(...),
    svc: DocumentIngestionService = Depends(_get_ingestion_service),
    auth: dict = Depends(require_auth),
) -> DocumentBatchUploadResponse:
    """여러 문서를 한 요청으로 업로드 — 등록마다 매번 새 연결을 맺어야 하는 부담을 줄인다.

    [2026-10-06] 각 파일은 POST /documents 와 동일하게 독립 처리된다(한 파일의 실패가
    나머지를 막지 않음 — results[i] 에 그 파일의 사유가 남는다). enqueue_classification=true
    면 성공적으로 적재된 문서 전체를 **하나의** POST /classify/batch 작업으로 묶어 큐에 건다
    (이미 운영 검증된 경로를 그대로 재사용 — 09-30 Celery 디스패치·시간제한 보강 적용분).
    documents_batch_max_files(기본 50) 를 넘는 요청은 413 — 파싱(HWP/PDF/DOCX 추출)이 요청
    안에서 동기로 끝나야 doc_id 를 돌려줄 수 있어, 상한을 높이면 이 요청 자체가 오래 걸린다.
    """
    try:
        actor_obj = Actor.model_validate(json.loads(actor))
    except (json.JSONDecodeError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"invalid actor json: {exc}") from exc
    bind_authenticated_actor(actor_obj, auth)

    if not files:
        raise HTTPException(status_code=422, detail="no files")
    max_files = max(1, int(getattr(settings, "documents_batch_max_files", 50) or 50))
    if len(files) > max_files:
        raise HTTPException(
            status_code=413,
            detail=f"too many files: {len(files)} > {max_files} per request. "
                   f"요청을 나눠 보낼 것(POST /documents/batch, documents_batch_max_files).",
        )
    if client_request_ids is not None and len(client_request_ids) != len(files):
        raise HTTPException(
            status_code=422,
            detail=f"client_request_ids length ({len(client_request_ids)}) "
                   f"must match files length ({len(files)})",
        )

    max_bytes = settings.max_upload_mb * 1024 * 1024
    results: list[DocumentUploadResponse] = []
    classify_targets: list[str] = []
    for i, file in enumerate(files):
        crid = client_request_ids[i] if client_request_ids is not None else None
        filename = file.filename or "unknown"
        declared_size = getattr(file, "size", None)
        if declared_size is not None and declared_size > max_bytes:
            results.append(_batch_item_error(
                filename, crid,
                f"file too large: {declared_size} > {max_bytes} bytes ({settings.max_upload_mb}MB)",
                file_size_bytes=declared_size,
            ))
            continue
        body = await file.read()
        if len(body) > max_bytes:
            results.append(_batch_item_error(
                filename, crid,
                f"file too large: {len(body)} > {max_bytes} bytes ({settings.max_upload_mb}MB)",
                file_size_bytes=len(body),
            ))
            continue
        if not body:
            results.append(_batch_item_error(filename, crid, "empty file"))
            continue
        resp = _ingest_to_response(
            svc,
            filename=filename,
            content_bytes=body,
            source_type=source_type,
            security_marking=security_marking,
            access_scope=access_scope,
            created_by=actor_obj.user_id,
            client_request_id=crid,
        )
        results.append(resp)
        if resp.persisted and resp.doc_id is not None and resp.char_count > 0:
            classify_targets.append(resp.doc_id)

    classification_job_id: Optional[str] = None
    classification_status: Optional[str] = None
    classification_status_url: Optional[str] = None
    if enqueue_classification:
        if not classify_targets:
            for r in results:
                r.warnings.append(
                    "classification was not queued: no document in this batch was persisted with extracted text"
                )
        else:
            try:
                from koipa.schemas.classify import ClassifyRequest  # noqa: PLC0415
                from koipa.schemas.classify_async import ClassifyBatchRequest  # noqa: PLC0415
                from koipa.services.async_classify_service import AsyncClassifyService  # noqa: PLC0415

                job = AsyncClassifyService().submit_batch(
                    ClassifyBatchRequest(
                        documents=[ClassifyRequest(doc_id=d) for d in classify_targets],
                        callback_url=callback_url,
                    )
                )
                classification_job_id = str(job.job_id)
                classification_status = job.status
                classification_status_url = job.status_url
            except Exception as exc:  # noqa: BLE001
                targets = set(classify_targets)
                for r in results:
                    if r.doc_id in targets:
                        r.warnings.append(f"classification was not queued: {type(exc).__name__}")

    return DocumentBatchUploadResponse(
        total=len(files),
        results=results,
        classification_job_id=classification_job_id,
        classification_status=classification_status,
        classification_status_url=classification_status_url,
    )


# ---------------------------------------------------------------------------
# POST /documents/analyze — 업로드 1회로 파싱→검수게이트→분류 전 구간을 단계별로 반환.
# 시연/관리자 콘솔이 "이 문서가 어떻게 파싱됐고, 어떤 게이트를 거쳐, 어떤 등급이 됐는지"를
# 한 화면에 보여주기 위한 백본. DB/스토리지 없이 in-process로 동작(persist=False, content 분류).
# 운영 적재 경로(POST /documents → POST /classify 본문 doc_id)와 별개의 read-only 진단 엔드포인트.
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
    char_count: int
    chunk_count: int
    pages_total: Optional[int] = None
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
    # [2026-08-23] 분류만 건너뛰는 opt-in 스위치. 기본 True — 기존 호출자 동작 불변.
    # False 면 아래 청크 상한 게이트(analyze_sync_max_chunks)도 함께 건너뛰고 추출·정규화·
    # PII·청킹까지만 돌려준다. 상한을 둔 이유는 **분류가 청크 수에 비례**하기 때문이고
    # (config.py 실측 고정비 2.9s + 0.82s/청크), 추출만 하면 그 비용이 없다.
    # 시연 콘솔이 413 을 받았을 때 이 스위치로 본문을 회수해 POST /classify/async 로 넘긴다.
    # 그 경로는 doc_id 가 비-UUID 면 영속화를 건너뛰므로(classify_service.py `_try_persist`)
    # 이 화면 전체에서 DB 적재가 발생하지 않는다 — POST /documents 를 타지 않는다.
    classify: bool = Form(default=True),
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
        char_count=len(pre.text),
        chunk_count=len(pre.chunks),
        pages_total=(getattr(ex, "total_pages", None) or getattr(ex, "pages", None)),
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
        + (f" · {ex.error}" if ex.error else ""),
        ms=_parse_ms,
    ))

    # [용량 게이트] 분류는 청크 수에 비례한다. 상한을 넘으면 여기서 거절한다 —
    # 그냥 진행하면 gunicorn --timeout 이 워커를 죽이고 클라이언트는 아무 설명 없이
    # 빈 응답을 받으며, --workers 1 이면 동시에 처리 중이던 다른 요청도 함께 끊긴다.
    # 조용히 죽는 것보다 즉시 이유를 말하고 거절하는 편이 낫다. 추출 결과는 이미
    # 나왔으므로 무엇이 얼마나 컸는지 숫자로 알려준다(문제 진단이 가능해야 한다).
    _chunk_cap = int(getattr(settings, "analyze_sync_max_chunks", 0) or 0)
    if classify and _chunk_cap > 0 and len(pre.chunks) > _chunk_cap:
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
                f"ANALYZE_SYNC_MAX_CHUNKS 를 조정할 것(접두사 없음)."
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
        error=ex.error,
        min_quality=float(getattr(settings, "extraction_review_min_quality", 0.6)),
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

    # [2026-08-23] classify=false — 추출 결과만 돌려주고 분류는 호출자가 비동기로 돌린다.
    # 청크 상한에 걸린 문서를 시연 콘솔이 이 경로로 다시 불러 본문을 받아간다.
    if not classify:
        resp.stages.append(AnalyzeStage(
            name="분류", status="skipped",
            detail=f"동기 분류 생략(classify=false) · 청크 {len(pre.chunks)}개 — 호출자가 비동기 분류",
        ))
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
    # 추출 검수게이트(표누락/저품질/추출오류)를 최종 status 로 조정 — content 경로 분류는
    # review_flagged 를 안 태우므로, 게이트가 검수를 요구하면 여기서 needs_review 로 승격해
    # box4('검수 필요')와 box5(최종 status)가 모순되지 않게 하고 실 doc_id 서빙과 일치시킨다.
    # [2026-08-21] `persistence skipped: doc_id=... is not a UUID` 를 화면 경고에서 뺀다.
    # 이 엔드포인트는 **설계상 저장하지 않는다**(위 주석: DB/스토리지 없이 in-process,
    # 운영 적재 경로 POST /documents → POST /classify(본문 doc_id) 와 별개인 read-only 진단).
    # doc_id 로 파일명을 넘기는 것도 의도된 것이고, 영속화 가드가 그것을 정상 거절한다.
    # 그런데 그 문구가 실제 문제(저신뢰·열화추출 등)와 같은 ⚠ 줄로 나란히 떠서, 사용자가
    # 무언가 실패한 것으로 읽었다(2026-08-21 지적). 설계대로 동작한 것을 경고로 알리지 않는다.
    # ⚠ `db unavailable` 등 **다른** persistence 경고는 남긴다 — 그건 진짜 이상 신호다.
    cls_warnings = [
        w for w in (cls.warnings or [])
        if "persistence skipped" not in w or "is not a UUID" not in w
    ]
    eff_status = cls.status
    if dec.requires_review and cls.status != "needs_review":
        eff_status = "needs_review"
        cls_warnings.append(
            "extraction_gate: 열화 추출(표누락/저품질)→검수 라우팅 (" + ", ".join(dec.reasons) + ")"
        )

    # [2026-08-24] 이 게이트는 classify 뒤에 와서 status 만 올리고 decision_path 는 분류기
    # 단계의 문장 그대로 내려보냈다 — 한 카드에 「검수 필요」와 「…자동 확정」이 같이 떴다.
    # 문구를 새로 만들지 않고 _decision_path 를 바뀐 status·경고로 다시 부른다
    # (classify_service.py:652 와 같은 말을 하게 한다). 표시용이라 실패해도 응답은 막지 않는다.
    decision_path = getattr(cls, "decision_path", None)
    if eff_status != cls.status:
        try:
            decision_path = ClassifyService._decision_path(cls, eff_status, cls_warnings)
        except Exception:  # noqa: BLE001
            pass
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
        decision_path=decision_path,
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


# ── 유사 문서 조회 ──────────────────────────────────────────────────────────
# [2026-09-09] 고객사 요청으로 되살린 기능. 2026-09-04(319069b9)에 RAG 를 걷으면서 함께
# 지웠던 자리인데, 그때 것과 다르다 — 질의응답(/answer)은 되살리지 않고 **문서 하나와
# 비슷한 문서를 찾는 것**만 만든다.
#
# 벡터와 등급이 같은 PostgreSQL 안에 있어 한 쿼리로 끝난다. 그 한 쿼리로 끝내려고
# MariaDB + 별도 Vector DB 대신 PostgreSQL + pgvector 로 되돌린 것이다.

class SimilarDocumentItem(BaseModel):
    doc_id: str
    filename: str
    similarity: float = Field(description="1 - 코사인거리. 1에 가까울수록 비슷하다")
    grade: Optional[str] = Field(default=None, description="확정 등급 코드(TS/S1/S2/S3). 미검수면 null")
    is_verified: bool = Field(description="사람이 확정한 등급인가")
    verified_at: Optional[str] = None


class SimilarDocumentsResponse(BaseModel):
    doc_id: str
    indexed: bool = Field(description="기준 문서가 색인돼 있는가. false 면 items 는 비어 있다")
    items: list[SimilarDocumentItem]


@router.get(
    "/documents/{doc_id}/similar",
    response_model=SimilarDocumentsResponse,
    summary="유사 문서 조회 (확정 등급 포함)",
)
def similar_documents(
    doc_id: str,
    k: int = 5,
    verified_only: bool = False,
) -> SimilarDocumentsResponse:
    """이 문서와 비슷한 문서를 등급과 함께 돌려준다.

    verified_only=true 면 **사람이 확정한 등급이 있는 문서만** 돌려준다. 화면이
    "비슷한 문서는 이 등급을 받았습니다"로 읽히는 자리에서는 이쪽을 써야 한다 —
    기계가 매긴 등급을 사람 판단의 근거처럼 보여주면 안 된다.

    기준 문서가 아직 색인되지 않았으면 indexed=false 와 빈 목록이다. **오류가 아니다** —
    업로드 직후에는 색인이 큐에 있고(청크당 0.51초), 코퍼스가 비어 있는 초기에는 비교할
    상대가 없다.
    """
    from koipa.adapters.vectorstore import DocumentVectorStore  # noqa: PLC0415

    k = max(1, min(int(k), 50))
    store = DocumentVectorStore()
    try:
        indexed = store.exists(doc_id)
        hits = store.similar(doc_id, k=k, verified_only=verified_only) if indexed else []
    except Exception as exc:  # noqa: BLE001
        # pgvector 미설치·표 없음 등 — 유사문서는 참고 기능이라 500 대신 사유를 남기고
        # 빈 결과를 준다. 분류·검수 화면이 이 실패로 멈추면 안 된다.
        raise HTTPException(
            status_code=503,
            detail=f"유사 문서 조회를 할 수 없습니다: {type(exc).__name__}. "
                   "pgvector 확장과 alembic 판(f8a9b0c1d2e3)이 적용됐는지 확인하십시오.",
        ) from exc
    return SimilarDocumentsResponse(
        doc_id=str(doc_id),
        indexed=indexed,
        items=[
            SimilarDocumentItem(
                doc_id=h.doc_id, filename=h.filename,
                similarity=round(h.similarity, 4),
                grade=h.grade, is_verified=h.is_verified, verified_at=h.verified_at,
            )
            for h in hits
        ],
    )
