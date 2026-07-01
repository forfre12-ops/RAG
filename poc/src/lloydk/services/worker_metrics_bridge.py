"""워커→API Prometheus 메트릭 브리지 (P0: 워커-프로세스 메트릭 no-data 해소).

배경: Prometheus 는 API 프로세스(job=lloydk-api)만 스크랩한다. 그러나 감사체인 무결성
검증(verify_audit_chain_tick)·임베딩 드리프트(drift_tick)는 Celery beat = **워커 프로세스**
에서만 계산된다. 워커의 prom 레지스트리는 별도 프로세스라 스크랩되지 않으므로, 이 신호들이
API /metrics-prom 에는 영원히 0/미변경으로 노출된다 → P0 AuditChainBroken 알람이 '거짓
all-clear', 운영 대시보드가 무음 '안전' 오표시(api/admin.py `_drift_snapshot`).

해결: 워커가 계산한 결과를 **워커·API 공유 매체인 Redis** 키에 실어 게시하고, API 의
`prom_metrics._refresh_business_gauges` 가 읽어 게이지로 재노출한다. (outbox·celery-queue
게이지가 이미 같은 원리로 Redis 를 직접 조회한다 — `_refresh_celery_queue_gauges`.)

전건 best-effort: Redis 미가용·미설치·테스트면 조용히 skip(신호 미갱신 → 거짓경보 없음).
게이지가 아니라 '현재 상태'를 싣는 이유: 크로스-프로세스에서 단조 카운터를 재구성할 수 없고,
알람이 필요로 하는 것은 '지금 깨졌는가'(현재 상태)이기 때문.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

_logger = logging.getLogger(__name__)

_KEY_PREFIX = "lloydk:metrics:"

# 신호 키 — 워커 tick 이 게시, API refresh 가 읽는다.
AUDIT_INTEGRITY = "audit_integrity"
DRIFT_REPORT = "drift_report"

# 게시값 TTL(초) — 워커가 죽어 갱신이 멈추면 만료돼 API 가 stale 값을 무한 신뢰하지 않게.
# 기본 tick 주기(감사=일별, drift=수십분)보다 넉넉히 크게 잡아 정상 운영에선 항상 존재.
_TTL_SEC = int(os.getenv("LLOYDK_METRICS_BRIDGE_TTL_SEC", str(48 * 3600)))


def _is_testing() -> bool:
    if os.getenv("TESTING", "").strip().lower() in {"1", "true", "yes", "on"}:
        return True
    return bool(os.getenv("PYTEST_CURRENT_TEST"))


def _get_redis():
    """공유 Redis 클라이언트 — best-effort. 미가용/미설치/테스트면 None.

    테스트는 이 함수를 monkeypatch 해 FakeRedis 를 주입한다(guard 우회).
    """
    if _is_testing():
        return None
    try:
        import redis  # noqa: PLC0415

        from lloydk.config import settings  # noqa: PLC0415

        return redis.Redis.from_url(
            settings.redis_url,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.debug("metrics bridge redis unavailable: %s", exc)
        return None


def publish_signal(name: str, payload: dict[str, Any]) -> bool:
    """워커에서 계산한 신호를 Redis 에 게시(best-effort). 성공 시 True.

    payload 에 checked_at(epoch) 을 자동 부가해 신선도 판정·대시보드에 쓸 수 있게 한다.
    """
    client = _get_redis()
    if client is None:
        return False
    try:
        body = dict(payload)
        body.setdefault("checked_at", time.time())
        client.set(_KEY_PREFIX + name, json.dumps(body), ex=_TTL_SEC)
        return True
    except Exception as exc:  # noqa: BLE001
        _logger.debug("metrics bridge publish skipped (%s): %s", name, exc)
        return False


def load_signal(name: str) -> dict[str, Any] | None:
    """API 에서 워커 게시 신호를 읽는다(best-effort). 부재/미가용/파싱실패면 None."""
    client = _get_redis()
    if client is None:
        return None
    try:
        raw = client.get(_KEY_PREFIX + name)
        if not raw:
            return None
        data = json.loads(raw)
        return data if isinstance(data, dict) else None
    except Exception as exc:  # noqa: BLE001
        _logger.debug("metrics bridge load skipped (%s): %s", name, exc)
        return None
