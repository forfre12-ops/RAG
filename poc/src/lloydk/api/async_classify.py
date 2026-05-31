"""/classify/async + /classify/batch + /classify/jobs/{id} + /classify/{doc_id} (최근 결과)."""

# future annotations 비활성: slowapi limiter가 함수 시그니처 forward-ref 평가에서 fail.

import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request

from lloydk.api._jwt_auth import require_auth
from lloydk.api.rate_limit import limiter
from lloydk.db import session_scope
from lloydk.repositories import ClassifyRepo
from lloydk.schemas.classify import ClassifyResponse
from lloydk.schemas.classify_async import (
    ClassifyAsyncRequest,
    ClassifyAsyncResponse,
    ClassifyBatchRequest,
    ClassifyBatchResponse,
    ClassifyJobStatus,
)
from lloydk.services.async_classify_service import AsyncClassifyService

router = APIRouter(tags=["classify"], dependencies=[Depends(require_auth)])


@router.post("/classify/async", response_model=ClassifyAsyncResponse, status_code=202)
@limiter.limit("60/minute")
def classify_async(request: Request, req: ClassifyAsyncRequest):
    return AsyncClassifyService().submit_async(req)


@router.post("/classify/batch", response_model=ClassifyBatchResponse, status_code=202)
@limiter.limit("60/minute")
def classify_batch(request: Request, req: ClassifyBatchRequest):
    # M1 경계값 가드: 빈 배열은 의미 없는 요청 → 400, 1000건 초과는 페이로드 거부 → 413.
    if len(req.documents) == 0:
        raise HTTPException(status_code=400, detail="documents must not be empty")
    if len(req.documents) > 1000:
        raise HTTPException(status_code=413, detail="batch size > 1000")
    return AsyncClassifyService().submit_batch(req)


@router.get("/classify/jobs/{job_id}", response_model=ClassifyJobStatus)
def classify_job_status(job_id: UUID):
    res = AsyncClassifyService().get_status(job_id)
    if res is None:
        raise HTTPException(status_code=404, detail="job not found")
    return res


@router.get("/classify/{doc_id}", response_model=ClassifyResponse)
def classify_recent_for_doc(doc_id: str):
    """doc_id의 최근 분류 결과 1건 (DB 진실 소스)."""
    try:
        doc_uuid = uuid.UUID(doc_id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=422, detail="doc_id must be a UUID") from exc
    try:
        with session_scope() as db:
            repo = ClassifyRepo(db)
            recent = repo.list_recent_for_doc(doc_uuid, limit=1)
            if not recent:
                raise HTTPException(status_code=404, detail="no classification for doc_id")
            cls = recent[0]
            # ClassificationLevel.level_code 조회
            from lloydk.db.models import ClassificationLevel  # noqa: PLC0415
            lvl = db.get(ClassificationLevel, cls.predicted_level_id)
            label = lvl.level_code if lvl else "S3"
            scores: dict[str, float] = {label: float(cls.confidence)}
            for alt in cls.alternatives or []:
                code = alt.get("level_code")
                if code:
                    scores[code] = float(alt.get("confidence", 0.0))
            return ClassifyResponse(
                inference_id=cls.classification_id,
                doc_id=doc_id,
                label=label,
                confidence=float(cls.confidence),
                scores=scores,
                model_version=cls.model_version,
                elapsed_ms=cls.inference_ms or 0,
                status=cls.status,
            )
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        # J2: 클라이언트에 내부 예외 메시지 노출하지 않음
        import logging as _logging
        _logging.getLogger(__name__).error("db unavailable: %s", exc, exc_info=True)
        raise HTTPException(status_code=503, detail="service temporarily unavailable") from exc
