"""Schema(등급체계) 도메인 스키마 (OpenAPI /schema/grades)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .common import Actor, GradeDefinition


class GradesGetResponse(BaseModel):
    version: str
    grades: list[GradeDefinition]


class GradesPutRequest(BaseModel):
    # `force`가 정책으로 확정된 4등급을 다시 우회하지 못하게, 알 수 없는 필드는
    # 무시하지 않고 422로 돌린다. 관리자 화면도 코드 추가·삭제 UI를 제공하지 않는다.
    model_config = ConfigDict(extra="forbid")

    grades: list[GradeDefinition]
    actor: Actor


class GradesPutResponse(BaseModel):
    version: str
    requires_retraining: bool
    reason: str
    # 이 변경이 서빙 분류기에 주는 영향. 빈 문자열이면 영향 없음.
    serving_impact: str = ""
