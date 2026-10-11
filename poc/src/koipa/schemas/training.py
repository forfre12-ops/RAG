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
    # 승인된 모의 문서는 기본 학습셋에 자동으로 섞지 않는다. 관리자가 새 학습을
    # 제출할 때만 명시적으로 포함을 요청하고, 워커가 후보 품질·홀드아웃 중복을 다시 확인한다.
    include_approved_synthetic: bool = False
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
    # 제출 시점에 동결한 승인 모의 문서의 공개 가능한 식별값만 노출한다.
    # 파일 경로·본문은 학습 노드 내부 증빙으로 남기고 API에는 싣지 않는다.
    approved_synthetic_snapshot: Optional[dict[str, Any]] = None


class TrainJobList(BaseModel):
    total: int
    items: list[TrainJobSummary]


# [2026-10-09] KL 요청(요구사항 추적표 6절) — "파인튜닝 완료 모델의 성능 지표를 리포트로
# 제공". 계산 자체(정확도·정밀도·재현율·F1·혼동행렬)는 trainer.py가 이미 하고
# ModelVersion.metrics(DB)에 저장돼 있었다 — API로 꺼내는 길이 없었을 뿐이다.
# [2026-10-11] 에폭별 손실·정확도 추이 그래프·PDF 내보내기 추가 완료(TrainingCurvePoint·
# _training_report_pdf.py).
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


class TrainingCurvePoint(BaseModel):
    """학습기가 실제로 남긴 한 시점의 손실·정확도 값.

    점이 없다는 것은 성능이 좋다는 뜻이 아니라, 과거 실행이 곡선을 보존하지
    않았다는 뜻이다. API와 PDF는 그 상태를 별도로 표시한다.
    """

    epoch: Optional[float] = None
    step: Optional[int] = None
    train_loss: Optional[float] = None
    eval_loss: Optional[float] = None
    accuracy: Optional[float] = None  # [2026-10-11] eval_accuracy — PDF의 에폭별 정확도 그래프가 읽는다


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
    learning_curve: list[TrainingCurvePoint] = Field(default_factory=list)
    learning_curve_status: str = "not_recorded"  # recorded | not_recorded
    compared_to_active: Optional[ModelMetricsComparison] = None
    raw_metrics: dict[str, Any] = Field(default_factory=dict)  # metrics dict 원본(화면이 모르는 새 필드도 안 잃음)
