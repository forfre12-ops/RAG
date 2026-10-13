"""상용 LLM 공급자(anthropic·openai) API 키 — 콘솔에서 재시작 없이 설정하는 런타임 저장소.

2026-10-11, 사용자 명시적 지시로 신설([[llm-credential-console-input-reversal-2026-10-11]] —
그 전까지는 "콘솔에 키 입력칸을 만들지 않는다"가 확정 금지였다, [[no-credential-prompt-ever-2026-08-24]]).
.env 의 ANTHROPIC_API_KEY·OPENAI_API_KEY 와 층이 다르다.
    .env 쪽   배포 시점에 고정하는 값 — 바꾸려면 서버 재시작.
    이 저장소  admin 이 콘솔에서 즉시 설정·교체 — 재시작 불필요, regulation 런타임 토글
               (runtime_toggle.py)과 같은 redis 기반 패턴.

값 자체는 redis 에만 있고, 조회용 status()는 평문을 절대 돌려주지 않는다(마지막 4자만).
로그에도 값·길이를 남기지 않는다 — 누가·어느 공급자를 바꿨는지만 남긴다.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_KEY_PREFIX = "koipa:llm:credential:"
SUPPORTED_PROVIDERS: tuple[str, ...] = ("anthropic", "openai")

_cached_client = None


def _client():
    global _cached_client
    if _cached_client is None:
        import redis  # noqa: PLC0415

        from koipa.config import settings  # noqa: PLC0415

        # runtime_toggle.py 와 같은 이유로 짧은 타임아웃 — redis 장애가 생성 호출 전체를
        # 초 단위로 묶어 두면 안 된다. 실패하면 호출부가 .env 기본값으로 fail back 한다.
        _cached_client = redis.Redis.from_url(
            settings.redis_url, decode_responses=True, socket_timeout=0.3, socket_connect_timeout=0.3
        )
    return _cached_client


def _key(provider: str) -> str:
    return _KEY_PREFIX + provider


def get_credential(provider: str, *, default: str = "") -> str:
    """빌드 경로 전용(build_provider() 내부 호출) — redis 값이 있으면 그걸, 없으면 default."""
    try:
        v = _client().get(_key(provider))
    except Exception as exc:  # noqa: BLE001 — redis 장애 시 .env 기본값으로
        logger.warning("LLM 키 조회 실패(%s) — .env 기본값으로 대체: %s: %s", provider, type(exc).__name__, exc)
        return default
    return v if v else default


def set_credential(value: str, *, provider: str, actor_id: str | None, actor_role: str | None) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"지원하지 않는 공급자: {provider}")
    value = (value or "").strip()
    if not value:
        raise ValueError("빈 값은 저장할 수 없습니다")
    _client().set(_key(provider), value)
    logger.info("LLM 키 설정(%s) actor=%s/%s", provider, actor_id, actor_role)


def clear_credential(provider: str, *, actor_id: str | None, actor_role: str | None) -> None:
    if provider not in SUPPORTED_PROVIDERS:
        raise ValueError(f"지원하지 않는 공급자: {provider}")
    _client().delete(_key(provider))
    logger.info("LLM 키 삭제(%s) actor=%s/%s", provider, actor_id, actor_role)


def status(provider: str, *, env_default: str) -> dict:
    """화면 표시용 — 평문을 절대 담지 않는다."""
    try:
        v = _client().get(_key(provider))
    except Exception as exc:  # noqa: BLE001
        logger.warning("LLM 키 상태 조회 실패(%s): %s: %s", provider, type(exc).__name__, exc)
        v = None
    if v:
        masked = ("…" + v[-4:]) if len(v) >= 4 else "설정됨"
        return {"configured": True, "source": "console", "masked": masked}
    if env_default:
        return {"configured": True, "source": "env", "masked": "서버 설정(.env)"}
    return {"configured": False, "source": "none", "masked": ""}


__all__ = [
    "SUPPORTED_PROVIDERS",
    "get_credential",
    "set_credential",
    "clear_credential",
    "status",
]
