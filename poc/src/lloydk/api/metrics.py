"""GET /metrics/* — 운영 모델 성능 지표 (W4 M6에서 풀로 채움)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from lloydk.api._jwt_auth import require_auth
from lloydk.schemas.metrics import ConfusionMatrix, MetricsHistory, MetricsReport
from lloydk.services.metrics_service import MetricsService

router = APIRouter(tags=["metrics"], dependencies=[Depends(require_auth)])


@router.get("/metrics/latest", response_model=MetricsReport)
def metrics_latest():
    res = MetricsService().latest()
    if res is None:
        raise HTTPException(status_code=404, detail="no active model")
    return res


@router.get("/metrics/history", response_model=MetricsHistory)
def metrics_history(limit: int = Query(default=20, ge=1, le=100)):
    return MetricsService().history(limit=limit)


@router.get("/metrics/confusion-matrix/{model_version}", response_model=ConfusionMatrix)
def metrics_cm(model_version: str):
    res = MetricsService().confusion_matrix(model_version)
    if res is None:
        raise HTTPException(status_code=404, detail="model_version not found")
    return res
