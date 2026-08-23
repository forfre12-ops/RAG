"""Metrics 도메인 스키마 (OpenAPI /metrics/*)."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class MetricsReport(BaseModel):
    model_version: str
    measured_at: Optional[str] = None
    accuracy: Optional[float] = None
    precision_macro: Optional[float] = None
    recall_macro: Optional[float] = None
    f1_macro: Optional[float] = None
    fnr_overall: Optional[float] = None
    fnr_by_grade: dict[str, float] = {}
    sample_count: Optional[int] = None
    # [2026-08-21] 이 숫자가 **어느 평가셋**에서 나왔는지. DB(tb_model_versions.metrics)
    #   에는 eval_source 가 이미 저장돼 있는데 API 가 안 내보내고 있었다.
    #   화면은 정확도 95.3% 만 보여 주고 근거 셋을 말하지 못했다 — 감리·시연 Q&A 에서
    #   "그 숫자는 무슨 셋이냐" 를 물으면 그 자리에서 답할 수 없다.
    #   ⚠ 제출본 대표값(hardened42)과 다른 셋일 수 있다. 두 수치를 섞어 말하지 않도록
    #     화면이 출처를 함께 띄운다.
    eval_source: Optional[str] = None
    fnr_high: Optional[float] = None


class MetricsHistory(BaseModel):
    items: list[MetricsReport]


class ConfusionMatrix(BaseModel):
    model_version: str
    labels: list[str]
    matrix: list[list[int]]
    fnr_by_grade: dict[str, float] = {}
