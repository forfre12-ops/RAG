import logging
import os
import time
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

_logger = logging.getLogger(__name__)


def _registry_ttl_sec() -> float:
    """레지스트리 캐시 TTL(초). LLOYDK_REGISTRY_CACHE_TTL_SEC 로 조정.

    M-cache (Track B): GradeRegistry/FactorRegistry 캐시는 프로세스-로컬 클래스변수라,
    schema PUT 후 invalidate()는 그 요청을 처리한 워커에만 적용된다. 다중 워커(gunicorn
    -w N, uvicorn --workers N)에서는 나머지 워커가 옛 등급/요소 매핑을 계속 쓴다 —
    영업비밀 등급 매핑이 잘못되면 미탐(비밀→공개)으로 이어질 수 있다.

    완화: 짧은 TTL을 둬, 다른 워커도 최대 TTL초 안에 DB에서 재로드하게 한다. 명시적
    invalidate()는 로컬 워커에서 즉시 반영(0초). updated_at 컬럼은 ORM UPDATE에서
    자동 bump되지 않으므로(onupdate 부재) max(updated_at) 버전스탬프는 deactivate/순서변경을
    못 잡는다 — 그래서 버전스탬프 대신 TTL을 진실원으로 쓴다.

    0 이하면 TTL 비활성(과거 동작: 명시 invalidate에만 의존).
    """
    raw = os.getenv("LLOYDK_REGISTRY_CACHE_TTL_SEC", "30").strip()
    try:
        return float(raw)
    except ValueError:
        return 30.0


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
    - schema PUT 후 invalidate()를 호출하면 다음 조회 시 재로드(로컬 워커 즉시)
    - 다중 워커: 짧은 TTL(_registry_ttl_sec)이 지나면 다른 워커도 DB에서 재로드
    """

    _cache: list[str] | None = None
    _cache_ts: float = 0.0  # 마지막 DB 로드 시각(time.monotonic). TTL 만료 판정용.

    @classmethod
    def _cache_fresh(cls) -> bool:
        """TTL 내이면 True. TTL<=0이면 항상 fresh(명시 invalidate에만 의존)."""
        ttl = _registry_ttl_sec()
        if ttl <= 0:
            return True
        return (time.monotonic() - cls._cache_ts) < ttl

    @classmethod
    def get_codes(cls) -> list[str]:
        """활성 등급 코드 목록 반환 (level_order 오름차순)."""
        if cls._cache is not None and cls._cache_fresh():
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
                    cls._cache_ts = time.monotonic()
                    _logger.debug("GradeRegistry loaded from DB: %s", cls._cache)
                    return list(cls._cache)
        except Exception as exc:  # noqa: BLE001
            _logger.debug("GradeRegistry DB load failed, using enum fallback: %s", exc)
        fallback = [g.value for g in Grade]
        cls._cache = fallback
        cls._cache_ts = time.monotonic()
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
        cls._cache_ts = 0.0
        _logger.info("GradeRegistry cache invalidated")


class FactorRegistry:
    """평가요소 코드를 DB에서 동적으로 로드하는 런타임 레지스트리.

    다른 프로젝트에서 evaluation_factors 테이블에 도메인별 요소를 등록하면
    코드 변경 없이 자동 반영됩니다.

    - DB 가용 시: evaluation_factors 테이블에서 활성 요소 로드
    - DB 미가용 시: 기본 4요소(영업비밀 도메인)로 폴백
    - schema PUT 후 invalidate()를 호출하면 다음 조회 시 재로드(로컬 워커 즉시)
    - 다중 워커: 짧은 TTL(_registry_ttl_sec)이 지나면 다른 워커도 DB에서 재로드
    """

    # 정본 3요건(S·V·M) — 영업비밀 도메인 폴백 (B안, doc/22 v2)
    _DEFAULT_MAP: dict[str, str] = {
        "SECRECY": "secrecy",
        "VALUE": "value",
        "MANAGEMENT": "management",
    }

    _cache: dict[str, str] | None = None  # {factor_code: snake_case_name}
    _cache_ts: float = 0.0  # 마지막 DB 로드 시각(time.monotonic). TTL 만료 판정용.

    @classmethod
    def _cache_fresh(cls) -> bool:
        """TTL 내이면 True. TTL<=0이면 항상 fresh(명시 invalidate에만 의존)."""
        ttl = _registry_ttl_sec()
        if ttl <= 0:
            return True
        return (time.monotonic() - cls._cache_ts) < ttl

    @classmethod
    def get_field_map(cls) -> dict[str, str]:
        """factor_code → snake_case 이름 매핑 반환.

        DB에 factor가 있으면 그것을 사용, 없으면 기본 4요소 반환.
        반환값은 EvaluationFactors.scores의 key와 named field 이름에 모두 사용됨.
        """
        if cls._cache is not None and cls._cache_fresh():
            return dict(cls._cache)
        try:
            from lloydk.db import session_scope  # noqa: PLC0415
            from lloydk.db.models import EvaluationFactor  # noqa: PLC0415
            with session_scope() as db:
                factors = (
                    db.query(EvaluationFactor)
                    .filter(EvaluationFactor.is_active.is_(True))
                    .all()
                )
                if factors:
                    cls._cache = {
                        f.factor_code: f.factor_code.lower()
                        for f in factors
                    }
                    cls._cache_ts = time.monotonic()
                    _logger.debug("FactorRegistry loaded from DB: %s", list(cls._cache))
                    return dict(cls._cache)
        except Exception as exc:  # noqa: BLE001
            _logger.debug("FactorRegistry DB load failed, using default: %s", exc)
        cls._cache = dict(cls._DEFAULT_MAP)
        cls._cache_ts = time.monotonic()
        return dict(cls._cache)

    @classmethod
    def get_codes(cls) -> list[str]:
        """활성 평가요소 코드 목록 반환."""
        return list(cls.get_field_map())

    @classmethod
    def invalidate(cls) -> None:
        """캐시 무효화 — schema PUT 후 호출해 다음 조회 시 재로드."""
        cls._cache = None
        cls._cache_ts = 0.0
        _logger.info("FactorRegistry cache invalidated")


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
    # 클라이언트 재시도 가능 여부 — True면 동일 요청 재시도가 성공할 수 있음
    # (timeout·일시적 백엔드 장애 등). False면 재시도 무의미(잘못된 입력·영구 오류).
    retryable: bool = False
