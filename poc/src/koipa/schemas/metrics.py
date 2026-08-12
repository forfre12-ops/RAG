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


class MetricsHistory(BaseModel):
    items: list[MetricsReport]


class ConfusionMatrix(BaseModel):
    model_version: str
    labels: list[str]
    matrix: list[list[int]]
    fnr_by_grade: dict[str, float] = {}
