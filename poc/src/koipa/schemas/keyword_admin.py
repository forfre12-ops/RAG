"""태깅(키워드·표현패턴) 관리 admin 스키마 — /admin/keywords CRUD (FUN-023).

요건: 키워드·표현패턴 태깅 규칙을 유연하게 수정·확장 관리. 데이터 모델(tb_level_keywords)과
읽기 경로(load_seeds_from_db → 룰엔진)는 이미 존재하고, 본 스키마는 그 위에 목록/추가/수정/
비활성(is_active) 관리 API 를 얹는다. 삭제는 하드 삭제 대신 is_active=false(소프트 삭제) —
FK RESTRICT 및 감사 보존과 정합(schema_admin 등급 비활성과 동일 정책).
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from .common import Actor

# 룰엔진 _count 가 지원하는 매칭 방식(rule_engine.py): exact(부분문자열)·regex·semantic(임베딩).
_PATTERN_TYPES = r"^(exact|regex|semantic)$"
# tb_level_keywords.weight = Numeric(3,2) → 최대 9.99.
_WEIGHT_MAX = 9.99


class KeywordItem(BaseModel):
    keyword_id: int
    grade: str
    keyword: str
    pattern_type: str
    factor: Optional[str] = None
    weight: float
    is_active: bool


class KeywordListResponse(BaseModel):
    keywords: list[KeywordItem]
    count: int
    # DB 미가용을 '빈 목록(0건)'과 구분 — 무음 실패 방지(review-queue 와 동일 규약).
    warnings: list[str] = Field(default_factory=list)


class KeywordCreateRequest(BaseModel):
    grade: str = Field(description="등급 코드(TS·S1·S2·S3 등) — 활성 등급이어야 함")
    keyword: str = Field(min_length=1, max_length=200)
    pattern_type: str = Field(default="exact", pattern=_PATTERN_TYPES)
    factor: Optional[str] = Field(
        default=None,
        description="평가요소 코드(정본 SECRECY·VALUE·MANAGEMENT 또는 레거시 4요소). 생략 시 NULL.",
    )
    weight: float = Field(default=1.0, ge=0.0, le=_WEIGHT_MAX)
    actor: Actor


class KeywordUpdateRequest(BaseModel):
    """부분 수정(PATCH) — 제공된 필드만 변경. is_active=false 로 비활성(소프트 삭제)."""

    keyword: Optional[str] = Field(default=None, min_length=1, max_length=200)
    pattern_type: Optional[str] = Field(default=None, pattern=_PATTERN_TYPES)
    factor: Optional[str] = None
    weight: Optional[float] = Field(default=None, ge=0.0, le=_WEIGHT_MAX)
    is_active: Optional[bool] = None
    actor: Actor


class KeywordMutationResponse(BaseModel):
    keyword_id: int
    action: str  # created | updated | deactivated
    item: Optional[KeywordItem] = None
    # 룰엔진 핫리로드 반영 여부(서빙 싱글턴). 다중 워커면 이 요청을 처리한 워커에만 즉시 반영
    # (GradeRegistry.invalidate 와 동일 한계 — 나머지 워커는 재기동/다음 리로드까지 stale).
    serving_reloaded: bool = False
    seed_count: Optional[int] = None
    note: str = ""
