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


class GradesPutResponse(BaseModel):
    version: str
    requires_retraining: bool
    reason: str
