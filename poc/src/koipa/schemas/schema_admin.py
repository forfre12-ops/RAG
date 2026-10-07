"""Schema(등급체계) 도메인 스키마 (OpenAPI /schema/grades)."""

from __future__ import annotations

from pydantic import BaseModel

from .common import Actor, GradeDefinition


class GradesGetResponse(BaseModel):
    version: str
    grades: list[GradeDefinition]


class GradesPutRequest(BaseModel):
    grades: list[GradeDefinition]
    actor: Actor
    # [2026-09-10] 서빙 모델과 등급 집합이 어긋나는 변경을 그래도 저장할 때만 쓴다.
    # 기본 False — 그런 변경은 409 로 거부된다(schema_admin_service.assess_grade_change).
    force: bool = False
    force_reason: str | None = None


class GradesPutResponse(BaseModel):
    version: str
    requires_retraining: bool
    reason: str
    # 이 변경이 서빙 분류기에 주는 영향. 빈 문자열이면 영향 없음.
    serving_impact: str = ""
