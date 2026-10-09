"""Training 도메인 스키마 (OpenAPI /train·/train/jobs)."""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from .common import Actor


class TrainRequest(BaseModel):
    training_type: str = Field(pattern=r"^(full|incremental|hyperparam_search)$")
    base_model: str = "kf-deberta-base"
    hyperparams: dict[str, Any] = Field(default_factory=dict)
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
    # 실패 사유. 워커는 tad_lm_lrn_excn_mng.error_message 에 남기는데 목록 응답에 없어서
    # 화면에는 status=failed 만 뜨고 **왜 실패했는지 알 방법이 없었다**(실측 2026-08-09:
    # "skipped: retrain topology unavailable (FileNotFoundError: datasets/labeled/train.jsonl)"
    # 가 DB 에 있었는데 화면엔 안 나왔다). 상세 조회로 넘어가지 않고 목록에서 바로 보이게 한다.
    error: Optional[str] = None


class TrainJobList(BaseModel):
    total: int
    items: list[TrainJobSummary]


# [2026-10-09] KL 요청(요구사항 추적표 6절) — "파인튜닝 완료 모델의 성능 지표를 리포트로
# 제공". 계산 자체(정확도·정밀도·재현율·F1·혼동행렬)는 trainer.py가 이미 하고
# ModelVersion.metrics(DB)에 저장돼 있었다 — API로 꺼내는 길이 없었을 뿐이다.
# ⚠ 에폭별 손실 추이 그래프·PDF 내보내기는 이번에 포함하지 않았다(에폭 단위 이력을 저장하는
# 자리가 현재 schema에 없음 — 별도 작업 필요, 메모리에 남김).
class ModelMetricsComparison(BaseModel):
    """파인튜닝 전(현재 활성 모델) 대비 비교 — 같은 모델이면 None."""

    version_label: str
    accuracy: Optional[float] = None
    precision_macro: Optional[float] = None
    recall_macro: Optional[float] = None
    f1_macro: Optional[float] = None
    fnr_overall: Optional[float] = None
    accuracy_delta: Optional[float] = None
    f1_macro_delta: Optional[float] = None
    fnr_overall_delta: Optional[float] = None  # 음수=개선(미탐 감소)


class TrainReportResponse(BaseModel):
    version_label: str
    base_model: Optional[str] = None
    trained_at: Optional[str] = None
    training_data_count: Optional[int] = None
    is_active: bool = False
    accuracy: Optional[float] = None
    precision_macro: Optional[float] = None
    recall_macro: Optional[float] = None
    f1_macro: Optional[float] = None
    fnr_overall: Optional[float] = None
    fnr_by_grade: dict[str, float] = Field(default_factory=dict)
    fnr_high: Optional[float] = None
    confusion_matrix: list[list[int]] = Field(default_factory=list)
    classification_report: str = ""
    compared_to_active: Optional[ModelMetricsComparison] = None
    raw_metrics: dict[str, Any] = Field(default_factory=dict)  # metrics dict 원본(화면이 모르는 새 필드도 안 잃음)
