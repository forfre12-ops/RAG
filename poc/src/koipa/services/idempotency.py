"""멱등성(Idempotency-Key) 저장소 — 동일 키 재요청 시 처리 1회 + 응답 재현.

변경성 POST/PUT(confirm·relabel·train·synth/generate 등)에서 클라이언트가
네트워크 타임아웃 등으로 같은 요청을 재시도해도 **중복 실행되지 않게** 한다.

저장소 우선순위:
1. redis (settings.redis_url) — 다중 워커 안전(권장).
2. in-memory 폴백 — redis 미가용 시. 프로세스-로컬이라 다중 워커에선 워커별 분리
   (정확도 제한). 단일 워커/개발 환경에선 정상 동작.

상태 모델:
- lock(in-flight): 처리 중. 다른 동일 키 요청은 409.
- result: 완료된 응답(status·body·content-type). 동일 키 재요청은 이 응답을 재현.
"""

from __future__ import annotations

import base64
import json
import logging
import threading
import time
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

_LOCK_TTL = 60          # in-flight 락 TTL(초) — 처리 지연 대비 자동 만료
_RESULT_TTL = 86400     # 완료 응답 보관 TTL(초) = 24h

# (status_code, body_bytes, media_type)
CachedResponse = Tuple[int, bytes, str]


class _MemoryStore:
    """프로세스-로컬 폴백. 다중 워커에선 워커별 분리 — redis 권장."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._locks: dict[str, float] = {}          # key -> lock expiry
        self._results: dict[str, tuple[CachedResponse, float]] = {}  # key -> (resp, expiry)

    def _purge(self, now: float) -> None:
        for k, (_, exp) in list(self._results.items()):
            if exp < now:
                self._results.pop(k, None)
        for k, exp in list(self._locks.items()):
            if exp < now:
                self._locks.pop(k, None)

    def get(self, key: str) -> Optional[CachedResponse]:
        with self._lock:
            now = time.time()
            self._purge(now)
            v = self._results.get(key)
            return v[0] if v else None

    def acquire(self, key: str) -> bool:
        with self._lock:
            now = time.time()
            self._purge(now)
            if key in self._results:
                return False
            if key in self._locks and self._locks[key] > now:
                return False
            self._locks[key] = now + _LOCK_TTL
            return True

    def store(self, key: str, resp: CachedResponse) -> None:
        with self._lock:
            self._results[key] = (resp, time.time() + _RESULT_TTL)
            self._locks.pop(key, None)

    def release(self, key: str) -> None:
        with self._lock:
            self._locks.pop(key, None)


class _RedisStore:
    def __init__(self, client) -> None:  # noqa: ANN001 — redis.Redis
        self._r = client

    @staticmethod
    def _rk(key: str) -> str:
        return f"idemp:res:{key}"

    @staticmethod
    def _lk(key: str) -> str:
        return f"idemp:lock:{key}"

    def get(self, key: str) -> Optional[CachedResponse]:
        raw = self._r.get(self._rk(key))
        if not raw:
            return None
        d = json.loads(raw)
        return (int(d["status"]), base64.b64decode(d["body"]), d.get("ctype") or "application/json")

    def acquire(self, key: str) -> bool:
        # 이미 완료된 결과가 있으면 락 불필요(get이 재현). in-flight 락만 NX로.
        if self._r.exists(self._rk(key)):
            return False
        return bool(self._r.set(self._lk(key), b"1", nx=True, ex=_LOCK_TTL))

    def store(self, key: str, resp: CachedResponse) -> None:
        status, body, ctype = resp
        payload = json.dumps({
            "status": status,
            "body": base64.b64encode(body).decode("ascii"),
            "ctype": ctype or "application/json",
        })
        self._r.set(self._rk(key), payload, ex=_RESULT_TTL)
        self._r.delete(self._lk(key))

    def release(self, key: str) -> None:
        self._r.delete(self._lk(key))


_store = None
_store_init_lock = threading.Lock()


def get_idempotency_store():
    """프로세스 단일 인스턴스 반환. redis 우선, 실패 시 in-memory 폴백."""
    global _store
    if _store is not None:
        return _store
    with _store_init_lock:
        if _store is not None:
            return _store
        try:
            import redis  # type: ignore  # noqa: PLC0415

            from koipa.config import settings  # noqa: PLC0415
            client = redis.Redis.from_url(
                settings.redis_url, decode_responses=False, socket_timeout=2
            )
            client.ping()
            _store = _RedisStore(client)
            logger.info("idempotency store: redis (%s)", settings.redis_url)
        except Exception as exc:  # noqa: BLE001 — redis 미설치/미가용 모두 폴백
            logger.warning(
                "idempotency store: in-memory fallback (redis unavailable: %s)", exc
            )
            # [정합성] 폴백을 메트릭으로 노출 — 멀티워커에선 워커 간 멱등성이 깨진다(best-effort).
            try:
                from koipa.api.prom_metrics import (  # noqa: PLC0415
                    IDEMPOTENCY_BACKEND_FALLBACK_TOTAL,
                )
                IDEMPOTENCY_BACKEND_FALLBACK_TOTAL.inc()
            except Exception:  # noqa: BLE001
                pass
            _store = _MemoryStore()
    return _store


def assert_multiworker_idempotency_safe() -> None:
    """[정합성] 멀티워커(WEB_CONCURRENCY>1) 운영에서 idempotency 저장소가 프로세스-로컬 메모리로
    폴백되면 워커 간 멱등성이 깨진다(중복 처리) — 그 조합이면 fail-fast.

    단일 워커는 no-op(메모리 폴백이라도 1워커면 정확). poc_mode=full·non-TESTING 게이트는
    호출부(assert_production_credentials)가 이미 적용하므로 여기선 워커 수만 본다.
    JWT 모드는 sub claim별 키 네임스페이스로 자연 격리되나, 저장소 자체가 프로세스-로컬이면
    여전히 워커 간 가시성이 없으므로 redis가 필요하다(인증모드 무관).
    """
    import os  # noqa: PLC0415
    try:
        workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
    except ValueError:
        workers = 1
    if workers <= 1:
        return
    if isinstance(get_idempotency_store(), _MemoryStore):
        raise RuntimeError(
            "정합성: WEB_CONCURRENCY>1(멀티워커) 운영인데 idempotency 저장소가 in-memory "
            "폴백입니다 — 워커 간 멱등성이 깨져 confirm/relabel 등 변경성 POST 재시도가 "
            "중복 처리될 수 있습니다. REDIS_URL을 가용 redis로 설정하세요(또는 WEB_CONCURRENCY=1)."
        )


def reset_idempotency_store_for_test() -> None:
    """테스트용 — 저장소 싱글톤 초기화."""
    global _store
    _store = None
