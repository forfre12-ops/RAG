"""/classify/async + /classify/batch + /classify/jobs/{id} + /classify/{doc_id} (최근 결과)."""

from __future__ import annotations

import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from lloydk.api._auth import require_api_key
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

router = APIRouter(tags=["classify"], dependencies=[Depends(require_api_key)])


@router.post("/classify/async", response_model=ClassifyAsyncResponse, status_code=202)
def classify_async(req: ClassifyAsyncRequest):
    return AsyncClassifyService().submit_async(req)


@router.post("/classify/batch", response_model=ClassifyBatchResponse, status_code=202)
def classify_batch(req: ClassifyBatchRequest):
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
        raise HTTPException(status_code=503, detail=f"db unavailable: {exc}") from exc
