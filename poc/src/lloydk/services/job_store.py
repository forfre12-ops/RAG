"""Job store for async tasks — in-memory + Redis 백엔드.

용도:
- /classify/async·/classify/batch 의 job 상태 추적
- /train/jobs/{id} 의 학습 상태 폴링 (DB의 TrainingRun도 진실 소스로 함께 사용)
- /synth/generate 의 생성 작업 추적

백엔드:
- ``InMemoryJobStore`` — 단일 프로세스용 (threading.Lock). 다중 워커에서 워커1
  생성 → 워커2 조회 시 NotFound 문제 발생. CI·dryrun·로컬 PoC 기본값.
- ``RedisJobStore`` — Celery 다중 워커 운영용. 키 ``lloydk:job:{job_id}``
  (TTL 24h), 값은 JSON. update는 WATCH/MULTI/EXEC로 race-free.

팩토리:
- ``get_job_store()`` — env ``JOB_STORE_BACKEND=redis|memory`` 우선,
  미지정 시 redis 연결 가능하면 redis, 아니면 in-memory. 연결 실패 시
  in-memory로 폴백 + ``logger.error``.
- ``get_default_store()`` — 기존 호출처(synthesis/training/async_classify)
  하위 호환용 alias. 모듈 로드 시 1회 팩토리 호출.
- ``JobStore`` — 기존 클래스명 alias (= ``InMemoryJobStore``). 외부 코드
  참조 보존.

설계 메모:
- redis 라이브러리는 lazy import (모듈 로드 시점 임포트 실패 차단).
- RedisJobStore는 ``list_recent``를 SCAN으로 best-effort 제공 (운영에서는
  Celery result backend / Postgres로 대체 권장).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from typing import Any, Optional, Protocol

logger = logging.getLogger(__name__)


def _emit_job_state_metric(status: object, created_at: object = None) -> None:
    """[obs] 작업 상태 진입 메트릭 — best-effort(실패는 분류·작업 경로 무영향).

    create/update의 단일 choke point에서 호출되어 상태 전이 1회당 1 inc(double-count 없음).
    터미널 상태(done/failed/partial)면 _created_at 기준 완료 소요시간도 관측. 메트릭
    레지스트리 미가용(일부 테스트)이면 조용히 skip.
    """
    try:
        from lloydk.api import prom_metrics as _pm  # noqa: PLC0415
        st = str(status)
        _pm.JOB_STATE_ENTERED_TOTAL.labels(state=st).inc()
        if st in ("done", "failed", "partial") and created_at is not None:
            dur = time.time() - float(created_at)
            if dur >= 0:
                _pm.JOB_COMPLETION_DURATION_SECONDS.labels(state=st).observe(dur)
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Protocol — 두 백엔드가 동일 인터페이스 제공함을 정적으로 명시
# ---------------------------------------------------------------------------


class JobStoreProtocol(Protocol):
    def create(self, job_id: uuid.UUID, payload: dict[str, Any]) -> None: ...

    def update(self, job_id: uuid.UUID, **fields: Any) -> None: ...

    def get(self, job_id: uuid.UUID) -> Optional[dict[str, Any]]: ...

    def set_status(self, job_id: uuid.UUID, status: str) -> None: ...

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# InMemory 백엔드
# ---------------------------------------------------------------------------


class InMemoryJobStore:
    """Thread-safe dict — process-local. 다중 워커 운영 시 RedisJobStore 사용."""

    def __init__(self):
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def create(self, job_id: uuid.UUID, payload: dict[str, Any]) -> None:
        # _created_at: 완료 소요시간(JOB_COMPLETION_DURATION) 산정용 — 터미널 상태에서 차감.
        with self._lock:
            self._jobs[str(job_id)] = {"status": "queued", "_created_at": time.time(), **payload}
        logger.debug("job_store create: job_id=%s keys=%s", job_id, list(payload.keys()))
        _emit_job_state_metric("queued")

    def update(self, job_id: uuid.UUID, **fields: Any) -> None:
        with self._lock:
            job = self._jobs.get(str(job_id))
            if job is None:
                logger.warning("job_store update: job not found — job_id=%s", job_id)
                return
            job.update(fields)
        # status 변동은 운영자 추적 가치 — info 수준
        if "status" in fields:
            logger.info(
                "job_store status: job_id=%s status=%s", job_id, fields.get("status"),
            )
            _emit_job_state_metric(fields.get("status"), job.get("_created_at"))
        else:
            logger.debug("job_store update: job_id=%s fields=%s", job_id, list(fields.keys()))

    def get(self, job_id: uuid.UUID) -> Optional[dict[str, Any]]:
        with self._lock:
            job = self._jobs.get(str(job_id))
            return dict(job) if job is not None else None

    def set_status(self, job_id: uuid.UUID, status: str) -> None:
        self.update(job_id, status=status)

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        # job_id 를 함께 실어 준다 — 레코드에는 id 가 없고(키로만 존재) 호출부가 목록에서
        # 개별 잡으로 진입할 수 없어 목록 자체가 무용해진다.
        with self._lock:
            items = list(self._jobs.items())[-limit:]
        return [{"job_id": k, **v} for k, v in items]


# 하위 호환: 기존 코드의 ``from job_store import JobStore`` 보존
JobStore = InMemoryJobStore


# ---------------------------------------------------------------------------
# Redis 백엔드
# ---------------------------------------------------------------------------


# TTL: 운영자가 24h 윈도우 안에서 job 상태를 폴링 가능. 그 이후는 Celery
# result backend / Postgres가 진실 소스.
_REDIS_JOB_TTL_SEC = 24 * 60 * 60
# [2026-08-07] 골든 검수 잡은 성격이 다르다 — 사람이 수백 건을 며칠에 걸쳐 검수한다.
# 24h 로는 이튿날 이어서 하려 할 때 잡 목록이 사라져 "어디까지 검수했는지"가 날아간다
# (실측: KL 서버에서 229건 잡이 24h 경과로 소멸 → 재등록 필요). 서명 결과(locked_gold_eval)는
# 파일이라 안전하지만 작업 목록은 보존돼야 한다. 종류별로 TTL 을 나눈다.
_REDIS_LONG_TTL_SEC = 30 * 24 * 60 * 60      # 사람 검수 주기(골든 빌드·등록)
_LONG_TTL_JOB_KINDS = frozenset({"golden_build", "golden_register"})
_REDIS_KEY_PREFIX = "lloydk:job:"


def _ttl_for(payload: dict[str, Any], default_ttl: int) -> int:
    """잡 종류에 맞는 TTL — 사람이 며칠 걸려 다루는 잡은 길게."""
    return _REDIS_LONG_TTL_SEC if payload.get("kind") in _LONG_TTL_JOB_KINDS else default_ttl


class RedisJobStore:
    """Redis 기반 job store — WATCH/MULTI/EXEC로 race-free update.

    Redis 라이브러리는 lazy import. ``__init__`` 시점에 ping으로 연결 확인.
    """

    def __init__(self, redis_url: str, *, ttl_seconds: int = _REDIS_JOB_TTL_SEC):
        # lazy import — 모듈 로드 시점 임포트 실패 차단
        import redis  # noqa: PLC0415

        self._redis_module = redis
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        # 연결 가능성 확인 (실패 시 호출자가 폴백 결정)
        self._client.ping()
        self._ttl = ttl_seconds
        self._url = redis_url
        logger.info("RedisJobStore connected: url=%s ttl=%ds", redis_url, ttl_seconds)

    @staticmethod
    def _key(job_id: uuid.UUID) -> str:
        return _REDIS_KEY_PREFIX + str(job_id)

    def create(self, job_id: uuid.UUID, payload: dict[str, Any]) -> None:
        key = self._key(job_id)
        # _created_at: 완료 소요시간 산정용(float, json default=str로 직렬화·TTL 내 보존).
        doc = {"status": "queued", "_created_at": time.time(), **payload}
        ttl = _ttl_for(payload, self._ttl)
        self._client.set(key, json.dumps(doc, default=str), ex=ttl)
        logger.debug(
            "redis_job_store create: job_id=%s kind=%s ttl=%ds keys=%s",
            job_id, payload.get("kind"), ttl, list(payload.keys()),
        )
        _emit_job_state_metric("queued")

    def update(self, job_id: uuid.UUID, **fields: Any) -> None:
        """WATCH/MULTI/EXEC 루프로 race-free merge.

        동시 update에서 한쪽이 실패하면 재시도 (최대 5회). 단순 GET+SET은
        last-writer-wins이라 동시 호출 시 일부 fields 유실 가능.
        """
        key = self._key(job_id)
        max_retries = 5
        for attempt in range(max_retries):
            with self._client.pipeline() as pipe:
                try:
                    pipe.watch(key)
                    raw = pipe.get(key)
                    if raw is None:
                        pipe.unwatch()
                        logger.warning(
                            "redis_job_store update: job not found — job_id=%s", job_id,
                        )
                        return
                    doc = json.loads(raw)
                    doc.update(fields)
                    pipe.multi()
                    # 갱신 때도 종류별 TTL 을 다시 적용 — 기본 TTL 로 덮으면 골든 잡이
                    # 상태 한 번 바뀔 때마다 24h 로 깎여 애써 늘린 보존기간이 무의미해진다.
                    pipe.set(key, json.dumps(doc, default=str), ex=_ttl_for(doc, self._ttl))
                    pipe.execute()
                    break
                except self._redis_module.WatchError:
                    # 동시 변경 — 재시도
                    if attempt == max_retries - 1:
                        logger.error(
                            "redis_job_store update: WATCH retry exhausted — job_id=%s",
                            job_id,
                        )
                        return
                    # 짧은 백오프 (jitter 불필요 — 재시도 5회로 충분)
                    time.sleep(0.01 * (attempt + 1))
                    continue
        if "status" in fields:
            logger.info(
                "redis_job_store status: job_id=%s status=%s",
                job_id, fields.get("status"),
            )
            # doc은 WATCH 루프(merge)에서 갱신된 최신 문서 — _created_at 보유.
            _emit_job_state_metric(fields.get("status"), doc.get("_created_at"))
        else:
            logger.debug(
                "redis_job_store update: job_id=%s fields=%s",
                job_id, list(fields.keys()),
            )

    def get(self, job_id: uuid.UUID) -> Optional[dict[str, Any]]:
        raw = self._client.get(self._key(job_id))
        if raw is None:
            return None
        return json.loads(raw)

    def set_status(self, job_id: uuid.UUID, status: str) -> None:
        self.update(job_id, status=status)

    def list_recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """SCAN 기반 best-effort. 정렬 순서는 Redis 키 스캔 순서 (FIFO 보장 X).

        다중 워커 환경에서 정확한 최근순이 필요하면 Celery result backend나
        Postgres TrainingRun 같은 진실 소스를 함께 조회.
        """
        out: list[dict[str, Any]] = []
        for key in self._client.scan_iter(match=_REDIS_KEY_PREFIX + "*", count=200):
            raw = self._client.get(key)
            if raw is None:
                continue
            try:
                doc = json.loads(raw)
            except json.JSONDecodeError:
                logger.warning("redis_job_store list_recent: bad json — key=%s", key)
            else:
                # 키 접두를 떼어 job_id 복원 — InMemory 백엔드와 동일 계약.
                k = key.decode() if isinstance(key, (bytes, bytearray)) else str(key)
                out.append({"job_id": k[len(_REDIS_KEY_PREFIX):], **doc})
            if len(out) >= limit:
                break
        return out


# ---------------------------------------------------------------------------
# 팩토리
# ---------------------------------------------------------------------------


def _select_backend(redis_url: str | None, env_backend: str | None) -> str:
    """선택 우선순위:
    1. env ``JOB_STORE_BACKEND`` 명시값 (``redis`` | ``memory``)
    2. redis_url 존재 → redis
    3. 그 외 → memory
    """
    if env_backend:
        choice = env_backend.strip().lower()
        if choice in ("redis", "memory"):
            return choice
        logger.warning(
            "JOB_STORE_BACKEND 알 수 없는 값=%s — memory 사용", env_backend,
        )
        return "memory"
    if redis_url:
        return "redis"
    return "memory"


def get_job_store(
    redis_url: Optional[str] = None,
    *,
    env_backend: Optional[str] = None,
) -> JobStoreProtocol:
    """JobStore 팩토리 — redis 또는 in-memory 백엔드 반환.

    Args:
        redis_url: redis 연결 URL. None이면 ``settings.redis_url`` 사용.
        env_backend: ``JOB_STORE_BACKEND`` override. None이면 env 조회.

    Returns:
        ``RedisJobStore`` 또는 ``InMemoryJobStore`` 인스턴스.

    Redis 연결 실패 시 ``InMemoryJobStore``로 자동 폴백 + ``logger.error``.
    """
    if redis_url is None:
        # lazy import로 import 시점 settings 강제 로드 회피
        try:
            from lloydk.config import settings  # noqa: PLC0415

            redis_url = settings.redis_url
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "get_job_store: settings 로드 실패 — memory 백엔드 사용 err=%s",
                type(exc).__name__,
            )
            return InMemoryJobStore()

    if env_backend is None:
        env_backend = os.environ.get("JOB_STORE_BACKEND")

    backend = _select_backend(redis_url, env_backend)
    if backend == "memory":
        logger.info("job_store backend: in-memory")
        return InMemoryJobStore()

    # backend == "redis"
    try:
        return RedisJobStore(redis_url)
    except ImportError as exc:
        logger.error(
            "job_store redis backend 요청됐으나 redis 라이브러리 부재 — "
            "in-memory 폴백 err=%s",
            exc,
        )
        return InMemoryJobStore()
    except Exception as exc:  # noqa: BLE001
        # redis 미가용 (네트워크/연결거부/timeout) — 폴백
        logger.error(
            "redis 연결 실패 — in-memory 폴백 url=%s err=%s",
            redis_url, type(exc).__name__,
        )
        return InMemoryJobStore()


def assert_multiworker_jobstore_safe() -> None:
    """[정합성] 멀티워커(WEB_CONCURRENCY>1) 운영에서 async job 저장소가 프로세스-로컬 메모리로
    폴백되면 워커 간 job 가시성이 깨진다 — 워커 A에 제출한 job을 워커 B가 조회하면 404/유실.
    그 조합이면 fail-fast(idempotency 저장소 가드와 동형).

    단일 워커는 no-op(메모리라도 1워커면 조회 일관). poc_mode=full·non-TESTING 게이트는
    호출부(assert_production_credentials)가 이미 적용하므로 여기선 워커 수만 본다.
    RedisJobStore.__init__ 이 ping()으로 eager connect 하므로, redis 요청됐으나 미가용이면
    get_job_store()가 InMemoryJobStore 로 폴백 → 여기서 잡힌다(연결성 검증 겸용).
    """
    import os  # noqa: PLC0415
    try:
        workers = int(os.environ.get("WEB_CONCURRENCY", "1"))
    except ValueError:
        workers = 1
    if workers <= 1:
        return
    if isinstance(get_job_store(), InMemoryJobStore):
        raise RuntimeError(
            "정합성: WEB_CONCURRENCY>1(멀티워커) 운영인데 async job 저장소가 in-memory "
            "폴백입니다 — 워커 간 job 가시성이 없어 제출/조회가 다른 워커면 유실됩니다. "
            "REDIS_URL을 가용 redis로 설정하세요(또는 WEB_CONCURRENCY=1)."
        )


# ---------------------------------------------------------------------------
# module-level singleton — 기존 호출처 호환
# ---------------------------------------------------------------------------


_default: Optional[JobStoreProtocol] = None
_default_lock = threading.Lock()


def get_default_store() -> JobStoreProtocol:
    """프로세스 전역 기본 store. 첫 호출 시 팩토리로 초기화.

    test에서 백엔드 교체가 필요하면 ``reset_default_store()`` 호출.
    """
    global _default
    if _default is None:
        with _default_lock:
            if _default is None:
                _default = get_job_store()
    return _default


def reset_default_store() -> None:
    """테스트용 — 다음 ``get_default_store()`` 호출에서 재초기화."""
    global _default
    with _default_lock:
        _default = None
