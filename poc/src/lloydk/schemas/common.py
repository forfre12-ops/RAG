import logging
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

_logger = logging.getLogger(__name__)


class Grade(str, Enum):
    TS = "TS"
    S1 = "S1"
    S2 = "S2"
    S3 = "S3"


class GradeDefinition(BaseModel):
    code: str
    name: str
    order: int
    description: Optional[str] = None
    color: Optional[str] = None


class GradeRegistry:
    """등급 코드를 DB에서 동적으로 로드하는 런타임 레지스트리.

    다른 프로젝트에서 TS/S1/S2/S3 이외의 등급체계를 사용할 때
    schema_admin API로 등급을 등록하면 자동으로 여기서 로드됩니다.

    - DB 가용 시: classification_levels 테이블에서 활성 등급을 level_order 순으로 로드
    - DB 미가용 시: Grade enum 기본값(TS/S1/S2/S3)으로 폴백
    - schema PUT 후 invalidate()를 호출하면 다음 조회 시 재로드
    """

    _cache: list[str] | None = None

    @classmethod
    def get_codes(cls) -> list[str]:
        """활성 등급 코드 목록 반환 (level_order 오름차순)."""
        if cls._cache is not None:
            return list(cls._cache)
        try:
            from lloydk.db import session_scope  # noqa: PLC0415
            from lloydk.db.models import ClassificationLevel  # noqa: PLC0415
            with session_scope() as db:
                levels = (
                    db.query(ClassificationLevel)
                    .filter(ClassificationLevel.is_active.is_(True))
                    .order_by(ClassificationLevel.level_order)
                    .all()
                )
                if levels:
                    cls._cache = [lv.level_code for lv in levels]
                    _logger.debug("GradeRegistry loaded from DB: %s", cls._cache)
                    return list(cls._cache)
        except Exception as exc:  # noqa: BLE001
            _logger.debug("GradeRegistry DB load failed, using enum fallback: %s", exc)
        fallback = [g.value for g in Grade]
        cls._cache = fallback
        return list(fallback)

    @classmethod
    def get_order(cls) -> dict[str, int]:
        """등급 코드 → 우선순위 정수 매핑 (낮을수록 높은 등급)."""
        return {code: i + 1 for i, code in enumerate(cls.get_codes())}

    @classmethod
    def to_grade(cls, code: str) -> "Grade | str":
        """코드 문자열 → Grade enum 변환. 알 수 없는 코드는 문자열 그대로 반환."""
        try:
            return Grade(code)
        except ValueError:
            return code

    @classmethod
    def invalidate(cls) -> None:
        """캐시 무효화 — schema_admin PUT 후 호출해 다음 조회 시 재로드."""
        cls._cache = None
        _logger.info("GradeRegistry cache invalidated")


class Actor(BaseModel):
    user_id: str
    role: str = Field(pattern=r"^(admin|reviewer|system|kl_backend)$")
    tenant_id: Optional[str] = None
    ip: Optional[str] = None


class Error(BaseModel):
    code: str
    message: str
    request_id: Optional[str] = None
    details: Optional[dict] = None
