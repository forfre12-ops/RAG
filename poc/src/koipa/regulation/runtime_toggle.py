"""규정 참고 표시 — 관리자가 콘솔에서 재시작 없이 켜고 끄는 런타임 스위치.

`config.regulation_reference_enabled`(.env)와는 층이 다르다.
    .env 쪽    이 배포에 규정 기능이 **설치돼 있는가**(API 라우터 존재) — 바꾸려면 서버 재시작.
    이 스위치  설치돼 있는 상태에서, **지금 검수 화면에 노출할지** — 콘솔 체크박스, 재시작 불필요.

Redis 키 하나(`koipa:regulation:runtime_enabled`)에 "0"/"1"을 둔다. 값이 없으면(첫 부팅 ·
아직 아무도 안 건드림) **켬으로 본다** — 이 스위치는 admin 이 명시적으로 끄는 옵트아웃이지,
새로 설치된 기능을 조용히 숨기는 옵트인이 아니다. Redis 장애 시에도 켬으로 fail-open 한다 —
검수 화면에 참고 문구 하나 덜 보이는 것이 검수 자체를 막는 것보다 안전하다(등급 판정에는
관여하지 않는 기능이라 fail-open 의 대가가 작다).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

_KEY = "koipa:regulation:runtime_enabled"
# LLM 옵션(해당되는 항만 고르기)의 런타임 스위치. 마스터 스위치(_KEY)와 기본값 방향이 다르다 —
# 이건 아직 아무도 안 건드렸으면(키 없음) **.env 의 REGULATION_LLM_SELECT_ENABLED 값을 그대로 따른다**
# (옵트아웃이 아니라 옵트인 성격의 기능이라, redis 장애·미설정 시 조용히 켜지면 안 된다).
_LLM_KEY = "koipa:regulation:llm_select_runtime_enabled"

_cached_client = None  # 호출마다 새로 접속하면 검수 조회 하나에 규정 후보 수만큼 redis 왕복이 늘어난다 — 재사용한다.


def _client():
    global _cached_client
    if _cached_client is None:
        import redis  # noqa: PLC0415

        from koipa.config import settings  # noqa: PLC0415

        _cached_client = redis.Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=2)
    return _cached_client


def is_enabled() -> bool:
    try:
        v = _client().get(_KEY)
    except Exception as exc:  # noqa: BLE001 — redis 장애는 켬으로 fail-open
        logger.warning("규정 런타임 스위치 조회 실패 — 켬으로 간주: %s: %s", type(exc).__name__, exc)
        return True
    return v != "0"


def set_enabled(value: bool, *, actor_id: str | None, actor_role: str | None) -> None:
    _client().set(_KEY, "1" if value else "0")
    logger.info("규정 런타임 스위치 %s (actor=%s/%s)", "켬" if value else "끔", actor_id, actor_role)


def is_llm_select_enabled(*, default: bool | None = None) -> bool:
    """redis 에 값이 없으면(관리자가 콘솔에서 아직 안 건드림) .env 기본값을 따른다.

    `default` 를 명시하지 않으면 전역 `koipa.config.settings` 를 읽는다 — 호출부가 이미 자기
    settings(`self._settings()`, 시험에서 주입 가능)를 들고 있으면 **그 값을 default 로 넘길 것**.
    전역을 여기서 다시 읽으면 시험이 주입한 설정을 무시하고 실제 프로세스 설정을 보게 된다
    (실측 2026-09-27 — 이 함수가 전역만 읽어 커스텀 settings 로 만든 서비스 시험이 깨졌다).
    """
    if default is None:
        from koipa.config import settings  # noqa: PLC0415
        default = bool(getattr(settings, "regulation_llm_select_enabled", False))
    env_default = default
    try:
        v = _client().get(_LLM_KEY)
    except Exception as exc:  # noqa: BLE001 — redis 장애 시 .env 기본값으로
        logger.warning("규정 LLM 스위치 조회 실패 — .env 기본값(%s)으로 간주: %s: %s",
                       env_default, type(exc).__name__, exc)
        return env_default
    if v is None:
        return env_default
    return v == "1"


def set_llm_select_enabled(value: bool, *, actor_id: str | None, actor_role: str | None) -> None:
    _client().set(_LLM_KEY, "1" if value else "0")
    logger.info("규정 LLM 스위치 %s (actor=%s/%s)", "켬" if value else "끔", actor_id, actor_role)


__all__ = ["is_enabled", "set_enabled", "is_llm_select_enabled", "set_llm_select_enabled"]
