"""POST /train + GET /train/jobs(/{id}) — 학습 트리거·상태 폴링."""

# slowapi limiter가 runtime에 함수 시그니처를 evaluate하므로 future annotations 비활성.
# (이슈: pydantic forward-ref가 데코레이터 wrap 시점에 해결되지 못함.)

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from lloydk.api._jwt_auth import require_auth
from lloydk.api.rate_limit import limiter
from lloydk.schemas.training import (
    TrainJobList,
    TrainRequest,
    TrainResponse,
    TrainStatus,
)
from lloydk.services.training_service import TrainingService

router = APIRouter(tags=["training"], dependencies=[Depends(require_auth)])


@router.post("/train", response_model=TrainResponse, status_code=202)
@limiter.limit("10/hour")
def train(request: Request, req: TrainRequest):
    return TrainingService().submit(req)


@router.get("/train/jobs/{train_job_id}", response_model=TrainStatus)
def train_status(train_job_id: UUID):
    res = TrainingService().status(train_job_id)
    if res is None:
        raise HTTPException(status_code=404, detail="train job not found")
    return res


@router.get("/train/jobs", response_model=TrainJobList)
def train_list(
    status: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),  # noqa: ARG001 — PoC, 페이지네이션 W4에서 보강
):
    return TrainingService().list_recent(limit=limit, status_filter=status)
