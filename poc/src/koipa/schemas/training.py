"""Training 도메인 스키마 (OpenAPI /train·/train/jobs)."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from .common import Actor


class TrainRequest(BaseModel):
    training_type: str = Field(pattern=r"^(full|incremental|hyperparam_search)$")
    base_model: str = "kf-deberta-base"
    dataset_version: Optional[str] = None
    hyperparams: dict[str, Any] = Field(default_factory=dict)
    use_rag: bool = False
    actor: Actor


class TrainResponse(BaseModel):
    train_job_id: UUID
    status_url: str
    websocket_url: Optional[str] = None


class TrainStatus(BaseModel):
    train_job_id: UUID
    status: str = Field(
        description="queued/preprocessing/training/evaluating/registering/done/failed"
    )
    progress: float = Field(ge=0.0, le=1.0, default=0.0)
    current_epoch: Optional[int] = None
    total_epochs: Optional[int] = None
    metrics_so_far: Optional[dict[str, Any]] = None
    started_at: Optional[str] = None
    estimated_finish_at: Optional[str] = None
    model_version: Optional[str] = None
    error: Optional[str] = None


class TrainJobSummary(BaseModel):
    train_job_id: UUID
    status: str
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    duration_sec: Optional[int] = None
    model_version: Optional[str] = None
    trigger_type: Optional[str] = None
    # 실패 사유. 워커는 tb_training_runs.error_message 에 남기는데 목록 응답에 없어서
    # 화면에는 status=failed 만 뜨고 **왜 실패했는지 알 방법이 없었다**(실측 2026-08-09:
    # "skipped: retrain topology unavailable (FileNotFoundError: datasets/labeled/train.jsonl)"
    # 가 DB 에 있었는데 화면엔 안 나왔다). 상세 조회로 넘어가지 않고 목록에서 바로 보이게 한다.
    error: Optional[str] = None


class TrainJobList(BaseModel):
    total: int
    items: list[TrainJobSummary]
