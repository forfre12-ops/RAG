"""P1-E3: Webhook Outbox 패턴 — KL 콜백 신뢰성.

문제: KL 측 webhook 호출 실패 시 단순 retry 만으로는 transactional consistency 보장 어려움.

해결: outbox 테이블/큐에 send-pending 메시지 적재 → worker가 폴링·재시도·DLQ 이동.

백엔드:
- ``InMemoryOutboxStore`` — 단일 프로세스용. CI·dryrun·로컬 PoC 기본.
- ``RedisOutboxStore`` — 다중 워커 운영. ZSET(score=next_retry_at) + HASH(payload)
  + STREAM(DLQ) 조합. WATCH/MULTI/EXEC로 race-free dequeue.

팩토리:
- ``get_outbox_store()`` — env ``OUTBOX_BACKEND=redis|memory`` 우선, 미지정 시
  settings.redis_url 가용하면 redis, 아니면 in-memory. 연결 실패 시 in-memory 폴백.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class OutboxMessage:
    id: str
    target_url: str
    payload: dict
    method: str = "POST"
    headers: dict = field(default_factory=dict)
    attempts: int = 0
    max_attempts: int = 5
    created_at: float = field(default_factory=time.time)
    last_error: Optional[str] = None
    next_retry_at: float = 0.0

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str, ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "OutboxMessage":
        d = json.loads(raw)
        return cls(**d)


class OutboxStore:
    """Outbox 추상. 구현체: InMemory / Redis."""

    name = "base"

    def enqueue(self, msg: OutboxMessage) -> None:
        raise NotImplementedError

    def dequeue_ready(self, *, limit: int = 10) -> list[OutboxMessage]:
        raise NotImplementedError

    def update(self, msg: OutboxMessage) -> None:
        raise NotImplementedError

    def move_to_dlq(self, msg: OutboxMessage) -> None:
        raise NotImplementedError

    def stats(self) -> dict:
        raise NotImplementedError


class InMemoryOutboxStore(OutboxStore):
    name = "memory"

    # in-flight visibility timeout — dequeue된 메시지는 이 시간 동안 보이지 않음
    # (Redis 백엔드의 invisible_until=now+600 과 동일). 워커 크래시 대비 자동 재진입.
    VISIBILITY_TIMEOUT_SEC = 600

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._items: dict[str, OutboxMessage] = {}
        self._dlq: dict[str, OutboxMessage] = {}

    def enqueue(self, msg: OutboxMessage) -> None:
        with self._lock:
            self._items[msg.id] = msg

    def dequeue_ready(self, *, limit: int = 10) -> list[OutboxMessage]:
        """ready 메시지의 **스냅샷(deepcopy)** 을 반환하며 in-flight 표시한다.

        M-outbox-inflight: Redis 백엔드와 동일하게, 반환 직전 락 아래에서
        ``next_retry_at = now + VISIBILITY_TIMEOUT_SEC`` 로 bump 해 두 번째
        워커가 같은 메시지를 즉시 다시 집지 못하게 한다(중복 webhook 차단).

        #35 (attempts 락 밖 변형 + visibility timeout 중 중복전송 차단):
        예전 구현은 store 가 보관한 *동일 객체 참조* 를 반환했다. 그러면
        deliver_once 가 락 밖에서 ``msg.attempts += 1`` / ``msg.next_retry_at=...``
        를 직접 변형할 때, visibility timeout 후 같은 객체를 다시 집은 다른
        워커와 attempts 가 경쟁하고(데이터 레이스), 성공 제거 직전 두 번째
        deliver 가 끼어들 수 있었다. 이를 막기 위해:
          1) 보관 객체는 락 안에서만 변형하고(아래 in-flight bump),
          2) 호출부에는 **deepcopy 스냅샷** 만 넘긴다. 호출부가 스냅샷을 락 밖에서
             아무리 변형해도 store 내부 상태와 경쟁하지 않는다. 실제 상태 반영은
             update()/move_to_dlq() 가 락 안에서 원자적으로 수행한다.
        """
        now = time.time()
        invisible_until = now + self.VISIBILITY_TIMEOUT_SEC
        snapshots: list[OutboxMessage] = []
        with self._lock:
            ready = [m for m in self._items.values() if m.next_retry_at <= now]
            ready.sort(key=lambda m: m.next_retry_at)
            picked = ready[:limit]
            for m in picked:
                # in-flight 표시 — 보관 객체에만 적용(락 안). visibility window 동안
                # 두 번째 dequeue 가 같은 메시지를 못 집는다.
                m.next_retry_at = invisible_until
                # 호출부에는 분리된 deepcopy 스냅샷을 전달(#35: 락 밖 변형 격리).
                snapshots.append(copy.deepcopy(m))
        return snapshots

    def update(self, msg: OutboxMessage) -> None:
        """deliver_once 가 만든 스냅샷의 새 상태를 락 안에서 원자적으로 반영.

        #35: attempts 증가/next_retry_at 갱신/성공 제거를 store 가 락 안에서
        한 번에 처리한다. 호출부(deliver_once)는 락 밖에서 스냅샷을 변형한 뒤
        이 원자 메서드만 호출한다. 메시지가 이미 제거(성공/DLQ)됐으면 무시해
        visibility window 내 중복 deliver 가 죽은 메시지를 되살리지 못하게 한다.
        """
        with self._lock:
            stored = self._items.get(msg.id)
            if stored is None:
                # 이미 성공 제거/ DLQ 이동된 메시지 — 스테일 업데이트 무시.
                return
            if msg.next_retry_at == float("inf"):
                # 성공 처리 → 즉시 제거(락 안 원자).
                self._items.pop(msg.id, None)
            else:
                merged = copy.deepcopy(msg)
                # #35: attempts 는 store 가 권위(authoritative)·단조 증가. 스테일
                # 스냅샷이 락 밖에서 들고 온 낮은 attempts 로 카운터를 되돌리지
                # 못하게 한다(visibility window 중 재진입/스테일 update 방어).
                merged.attempts = max(stored.attempts, msg.attempts)
                self._items[msg.id] = merged

    def move_to_dlq(self, msg: OutboxMessage) -> None:
        with self._lock:
            # #35: 이미 제거된 메시지면 중복 DLQ 적재 방지(원자 처리).
            if msg.id not in self._items and msg.id in self._dlq:
                return
            self._items.pop(msg.id, None)
            self._dlq[msg.id] = copy.deepcopy(msg)

    def stats(self) -> dict:
        with self._lock:
            return {"backend": "memory", "pending": len(self._items), "dlq": len(self._dlq)}


# ---------------------------------------------------------------------------
# Redis 백엔드
# ---------------------------------------------------------------------------

_OUTBOX_PENDING_ZSET = "lloydk:outbox:pending"     # ZSET: msg_id → score=next_retry_at
_OUTBOX_PAYLOAD_HASH = "lloydk:outbox:payload"     # HASH: msg_id → JSON
_OUTBOX_DLQ_STREAM = "lloydk:outbox:dlq"           # STREAM: 영구 보존
_OUTBOX_MSG_TTL_SEC = 7 * 24 * 60 * 60             # payload 보존 7일 (재시도 윈도우 + 디버깅)


class RedisOutboxStore(OutboxStore):
    """Redis 기반 — ZSET(스케줄) + HASH(payload) + STREAM(DLQ).

    Why ZSET: score=next_retry_at로 ready 메시지를 O(log N) 조회.
    Why HASH: 본문은 HASH에 분리 보관 → ZSET은 가벼움 유지.
    Why STREAM(DLQ): 영구 audit trail + consumer group으로 운영자 처리 가능.

    동시성: dequeue_ready는 WATCH + ZRANGEBYSCORE + ZREM으로 같은 메시지를
    두 워커가 동시에 잡지 못하게 보장. 실패하면 다른 워커가 재시도 윈도우에서 다시 잡음.
    """

    name = "redis"

    def __init__(self, redis_url: str, *, msg_ttl_seconds: int = _OUTBOX_MSG_TTL_SEC):
        import redis  # noqa: PLC0415

        self._redis_module = redis
        self._client = redis.Redis.from_url(redis_url, decode_responses=True)
        self._client.ping()
        self._ttl = msg_ttl_seconds
        self._url = redis_url
        logger.info("RedisOutboxStore connected: url=%s ttl=%ds", redis_url, msg_ttl_seconds)

    def enqueue(self, msg: OutboxMessage) -> None:
        with self._client.pipeline() as pipe:
            pipe.hset(_OUTBOX_PAYLOAD_HASH, msg.id, msg.to_json())
            pipe.zadd(_OUTBOX_PENDING_ZSET, {msg.id: msg.next_retry_at or time.time()})
            pipe.execute()

    def dequeue_ready(self, *, limit: int = 10) -> list[OutboxMessage]:
        """ZRANGEBYSCORE로 ready 후보 → WATCH + 개별 ZREM으로 락 획득.

        획득한 메시지는 dequeue 후 즉시 score를 매우 큰 값(처리 중)으로 갱신해
        다른 워커가 중복 처리 못 하게 한다. 호출자가 update()로 next_retry_at을
        다시 정상값으로 되돌리거나 move_to_dlq로 옮긴다.
        """
        now = time.time()
        ids = self._client.zrangebyscore(
            _OUTBOX_PENDING_ZSET, min=0, max=now, start=0, num=limit,
        )
        out: list[OutboxMessage] = []
        # in-flight 표시 (10분 후 자동 재진입) — 워커 크래시 대비 visibility timeout
        invisible_until = now + 600
        for mid in ids:
            with self._client.pipeline() as pipe:
                try:
                    pipe.watch(_OUTBOX_PENDING_ZSET)
                    score = pipe.zscore(_OUTBOX_PENDING_ZSET, mid)
                    if score is None or score > now:
                        pipe.unwatch()
                        continue
                    raw = pipe.hget(_OUTBOX_PAYLOAD_HASH, mid)
                    if raw is None:
                        # payload 사라짐 — zset 정리
                        pipe.multi()
                        pipe.zrem(_OUTBOX_PENDING_ZSET, mid)
                        pipe.execute()
                        continue
                    pipe.multi()
                    pipe.zadd(_OUTBOX_PENDING_ZSET, {mid: invisible_until})
                    pipe.execute()
                    out.append(OutboxMessage.from_json(raw))
                except self._redis_module.WatchError:
                    continue
        return out

    def update(self, msg: OutboxMessage) -> None:
        if msg.next_retry_at == float("inf"):
            # 성공 → 영구 제거
            with self._client.pipeline() as pipe:
                pipe.hdel(_OUTBOX_PAYLOAD_HASH, msg.id)
                pipe.zrem(_OUTBOX_PENDING_ZSET, msg.id)
                pipe.execute()
            return
        with self._client.pipeline() as pipe:
            pipe.hset(_OUTBOX_PAYLOAD_HASH, msg.id, msg.to_json())
            pipe.zadd(_OUTBOX_PENDING_ZSET, {msg.id: msg.next_retry_at})
            pipe.execute()

    def move_to_dlq(self, msg: OutboxMessage) -> None:
        """STREAM에 append + pending에서 제거.

        STREAM 보존 정책은 운영자 결정 (XSETID/MAXLEN). 본 함수는 unbounded append.
        """
        entry = {
            "msg_id": msg.id,
            "target_url": msg.target_url,
            "attempts": str(msg.attempts),
            "last_error": msg.last_error or "",
            "payload_json": json.dumps(msg.payload, default=str, ensure_ascii=False),
        }
        with self._client.pipeline() as pipe:
            pipe.xadd(_OUTBOX_DLQ_STREAM, entry)
            pipe.hdel(_OUTBOX_PAYLOAD_HASH, msg.id)
            pipe.zrem(_OUTBOX_PENDING_ZSET, msg.id)
            pipe.execute()

    def stats(self) -> dict:
        pending = self._client.zcard(_OUTBOX_PENDING_ZSET)
        try:
            dlq = self._client.xlen(_OUTBOX_DLQ_STREAM)
        except self._redis_module.ResponseError:
            # stream 미생성 시
            dlq = 0
        return {"backend": "redis", "pending": int(pending), "dlq": int(dlq)}


# ---------------------------------------------------------------------------
# 비즈니스 API
# ---------------------------------------------------------------------------


def publish(
    store: OutboxStore,
    *,
    target_url: str,
    payload: dict,
    headers: Optional[dict] = None,
    max_attempts: int = 5,
) -> OutboxMessage:
    msg = OutboxMessage(
        id=str(uuid.uuid4()),
        target_url=target_url,
        payload=payload,
        headers=headers or {},
        max_attempts=max_attempts,
        next_retry_at=time.time(),
    )
    store.enqueue(msg)
    return msg


def _backoff_seconds(attempts: int) -> float:
    return min(2 ** attempts, 600)


def deliver_once(
    store: OutboxStore,
    *,
    http_send: Callable[[str, str, dict, dict], int],
    limit: int = 10,
) -> dict:
    """ready 메시지 한 batch 전송. Worker(또는 Celery beat)가 주기 호출.

    Args:
        store: OutboxStore 구현체
        http_send: callable(url, method, headers, payload) -> http_status_code
        limit: 1회 처리 최대 건수

    Returns:
        {"sent": n, "failed": n, "dlq": n, "ready": n_dequeued}
    """
    ready = store.dequeue_ready(limit=limit)
    sent = failed = dlq = 0
    for msg in ready:
        msg.attempts += 1
        success = False
        try:
            status = http_send(msg.target_url, msg.method, msg.headers, msg.payload)
            if 200 <= status < 300:
                success = True
            else:
                msg.last_error = f"http_{status}"
        except Exception as e:  # noqa: BLE001
            msg.last_error = f"{type(e).__name__}:{e}"[:200]

        if success:
            msg.last_error = None
            msg.next_retry_at = float("inf")
            store.update(msg)
            sent += 1
            continue

        if msg.attempts >= msg.max_attempts:
            logger.warning(
                "outbox DLQ: id=%s url=%s attempts=%d err=%s",
                msg.id, msg.target_url, msg.attempts, msg.last_error,
            )
            store.move_to_dlq(msg)
            dlq += 1
        else:
            msg.next_retry_at = time.time() + _backoff_seconds(msg.attempts)
            store.update(msg)
            failed += 1
    return {"sent": sent, "failed": failed, "dlq": dlq, "ready": len(ready)}


def http_send_via_httpx(url: str, method: str, headers: dict, payload: dict) -> int:
    """기본 송신자 — httpx 사용. 운영시 timeout/retry 정책 적용."""
    import httpx  # type: ignore

    with httpx.Client(timeout=10.0) as client:
        resp = client.request(method, url, headers=headers, json=payload)
        return resp.status_code


# ---------------------------------------------------------------------------
# 팩토리
# ---------------------------------------------------------------------------


def _select_backend(redis_url: str | None, env_backend: str | None) -> str:
    if env_backend:
        choice = env_backend.strip().lower()
        if choice in ("redis", "memory"):
            return choice
        logger.warning("OUTBOX_BACKEND 알 수 없는 값=%s — memory 사용", env_backend)
        return "memory"
    if redis_url:
        return "redis"
    return "memory"


_default_store: OutboxStore | None = None
_default_lock = threading.Lock()


def get_outbox_store(
    redis_url: Optional[str] = None,
    *,
    env_backend: Optional[str] = None,
) -> OutboxStore:
    """Outbox 팩토리. Redis 연결 실패 시 InMemory 폴백."""
    global _default_store
    if _default_store is not None:
        return _default_store

    with _default_lock:
        if _default_store is not None:
            return _default_store

        if redis_url is None:
            try:
                from lloydk.config import settings  # noqa: PLC0415
                redis_url = settings.redis_url
            except Exception as exc:  # noqa: BLE001
                logger.warning("outbox: settings 로드 실패 — memory err=%s", type(exc).__name__)
                _default_store = InMemoryOutboxStore()
                return _default_store

        if env_backend is None:
            env_backend = os.environ.get("OUTBOX_BACKEND")

        backend = _select_backend(redis_url, env_backend)
        if backend == "memory":
            logger.info("outbox backend: in-memory")
            _default_store = InMemoryOutboxStore()
            return _default_store

        try:
            _default_store = RedisOutboxStore(redis_url)
            return _default_store
        except ImportError as exc:
            logger.error("outbox redis 요청됐으나 redis 라이브러리 부재 — memory 폴백 err=%s", exc)
            _default_store = InMemoryOutboxStore()
            return _default_store
        except Exception as exc:  # noqa: BLE001
            logger.error("outbox redis 연결 실패 — memory 폴백 url=%s err=%s",
                         redis_url, type(exc).__name__)
            _default_store = InMemoryOutboxStore()
            return _default_store


def reset_default_store() -> None:
    """테스트용."""
    global _default_store
    with _default_lock:
        _default_store = None
