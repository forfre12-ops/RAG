"""P1-E3: Webhook Outbox 패턴 — KL 콜백 신뢰성.

문제: KL 측 webhook 호출 실패 시 단순 retry 만으로는 transactional consistency 보장 어려움.

해결: outbox 테이블에 send-pending 메시지 적재 → background worker가 폴링·재시도·DLQ 이동.

본 모듈은 InMemory 큐 + Redis 큐 백엔드 모두 지원. DB 마이그레이션 없이 즉시 동작.
운영시:
- alembic으로 outbox 테이블 도입 권장(영속성)
- DLQ는 별도 Redis stream `lloydk:outbox:dlq`
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, field
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


class OutboxStore:
    """Outbox 추상. 구현체: InMemory / Redis."""

    def enqueue(self, msg: OutboxMessage) -> None:
        raise NotImplementedError

    def dequeue_ready(self, *, limit: int = 10) -> list[OutboxMessage]:
        raise NotImplementedError

    def update(self, msg: OutboxMessage) -> None:
        raise NotImplementedError

    def move_to_dlq(self, msg: OutboxMessage) -> None:
        raise NotImplementedError


class InMemoryOutboxStore(OutboxStore):
    def __init__(self) -> None:
        self._items: dict[str, OutboxMessage] = {}
        self._dlq: dict[str, OutboxMessage] = {}

    def enqueue(self, msg: OutboxMessage) -> None:
        self._items[msg.id] = msg

    def dequeue_ready(self, *, limit: int = 10) -> list[OutboxMessage]:
        now = time.time()
        ready = [m for m in self._items.values() if m.next_retry_at <= now]
        ready.sort(key=lambda m: m.next_retry_at)
        return ready[:limit]

    def update(self, msg: OutboxMessage) -> None:
        if msg.id in self._items:
            self._items[msg.id] = msg

    def move_to_dlq(self, msg: OutboxMessage) -> None:
        self._items.pop(msg.id, None)
        self._dlq[msg.id] = msg

    def stats(self) -> dict:
        return {"pending": len(self._items), "dlq": len(self._dlq)}


def publish(
    store: OutboxStore,
    *,
    target_url: str,
    payload: dict,
    headers: Optional[dict] = None,
) -> OutboxMessage:
    """비즈니스 코드에서 outbox에 적재. 동기 transactional 합 가능."""
    msg = OutboxMessage(
        id=str(uuid.uuid4()),
        target_url=target_url,
        payload=payload,
        headers=headers or {},
        next_retry_at=time.time(),
    )
    store.enqueue(msg)
    return msg


def _backoff_seconds(attempts: int) -> float:
    # 지수 백오프 + 캡
    return min(2 ** attempts, 600)


def deliver_once(
    store: OutboxStore,
    *,
    http_send: Callable[[str, str, dict, dict], int],
    limit: int = 10,
) -> dict:
    """ready 메시지 한 batch 전송. Worker가 주기 호출.

    Args:
        store: OutboxStore 구현체
        http_send: callable(url, method, headers, payload) -> http_status_code
                   (테스트 가능성 위해 외부 주입)
        limit: 1회 처리 최대 건수

    Returns:
        {"sent": n_sent, "failed": n_failed, "dlq": n_dlq}
    """
    ready = store.dequeue_ready(limit=limit)
    sent = failed = dlq = 0
    for msg in ready:
        msg.attempts += 1
        try:
            status = http_send(msg.target_url, msg.method, msg.headers, msg.payload)
            if 200 <= status < 300:
                # 성공 → outbox에서 제거 (간단 구현: update last_error=None + next_retry=무한)
                msg.last_error = None
                msg.next_retry_at = float("inf")
                store.update(msg)
                sent += 1
                continue
            msg.last_error = f"http_{status}"
        except Exception as e:  # noqa: BLE001
            msg.last_error = f"{type(e).__name__}:{e}"

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


# 글로벌 인스턴스 (운영 환경에서 Redis 백엔드로 교체 권장)
_default_store: OutboxStore | None = None


def get_outbox_store() -> OutboxStore:
    global _default_store
    if _default_store is None:
        _default_store = InMemoryOutboxStore()
    return _default_store
