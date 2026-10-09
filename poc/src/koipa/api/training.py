"""POST /train + GET /train/jobs(/{id}) — 학습 트리거·상태 폴링."""

# slowapi limiter가 runtime에 함수 시그니처를 evaluate하므로 future annotations 비활성.
# (이슈: pydantic forward-ref가 데코레이터 wrap 시점에 해결되지 못함.)

import csv
import io
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from koipa.api._jwt_auth import require_auth
from koipa.api.confirm import bind_authenticated_actor
from koipa.api.rate_limit import limiter
from koipa.schemas.training import (
    TrainJobList,
    TrainRequest,
    TrainResponse,
    TrainStatus,
)
from koipa.services.training_service import TrainingService

router = APIRouter(tags=["training"], dependencies=[Depends(require_auth)])


@router.post("/train", response_model=TrainResponse, status_code=202)
@limiter.limit("10/hour")
def train(request: Request, req: TrainRequest, auth: dict = Depends(require_auth)):
    # [#13] created_by 감사 신원을 인증 principal 로 확정(body 자칭 위조 차단; JWT sub 우선).
    bind_authenticated_actor(req.actor, auth)
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
    offset: int = Query(default=0, ge=0),
):
    return TrainingService().list_recent(limit=limit, status_filter=status, offset=offset)


# [2026-10-09] KL 요청(요구사항 추적표 6절) — 파인튜닝 모델 성능 리포트.
# ⚠ 에폭별 손실 추이·PDF 내보내기는 범위 밖(별도 작업 필요, 메모리 참조). 이 엔드포인트는
# 이미 계산·저장된 지표(정확도·정밀도·재현율·F1·혼동행렬)를 꺼내고, 현재 활성 모델과
# 비교해 개선폭을 더한다. CSV는 format=csv 로 받는다.
@router.get("/train/models/{version_label}/report", response_model=None)
def train_report(version_label: str, format: str = Query(default="json", pattern=r"^(json|csv)$")):
    rep = TrainingService().report(version_label)
    if rep is None:
        raise HTTPException(status_code=404, detail="model version not found")
    if format == "json":
        return rep
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["metric", "value"])
    for k in ("version_label", "base_model", "trained_at", "training_data_count", "is_active",
              "accuracy", "precision_macro", "recall_macro", "f1_macro", "fnr_overall", "fnr_high"):
        w.writerow([k, getattr(rep, k)])
    if rep.compared_to_active:
        c = rep.compared_to_active
        w.writerow(["compared_to_active_version", c.version_label])
        w.writerow(["accuracy_delta", c.accuracy_delta])
        w.writerow(["f1_macro_delta", c.f1_macro_delta])
        w.writerow(["fnr_overall_delta", c.fnr_overall_delta])
    for grade, fnr in rep.fnr_by_grade.items():
        w.writerow([f"fnr_{grade}", fnr])
    buf.seek(0)
    return StreamingResponse(
        buf, media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="train_report_{version_label}.csv"'},
    )
