"""P1-E3: webhook outbox 패턴 검증."""

from __future__ import annotations

from lloydk.services.outbox import (
    InMemoryOutboxStore,
    deliver_once,
    publish,
)


def test_publish_enqueues_message():
    store = InMemoryOutboxStore()
    msg = publish(store, target_url="http://kl/cb", payload={"x": 1})
    assert msg.id
    assert store.stats()["pending"] == 1


def test_deliver_success_removes_from_pending():
    store = InMemoryOutboxStore()
    publish(store, target_url="http://kl/cb", payload={"x": 1})

    def ok(url, method, headers, payload):
        return 200

    r = deliver_once(store, http_send=ok)
    assert r["sent"] == 1
    assert r["failed"] == 0
    assert r["dlq"] == 0


def test_deliver_failure_increments_attempts():
    store = InMemoryOutboxStore()
    publish(store, target_url="http://kl/cb", payload={"x": 1})

    def fail(url, method, headers, payload):
        return 500

    r = deliver_once(store, http_send=fail)
    assert r["failed"] == 1
    assert r["dlq"] == 0


def test_max_attempts_moves_to_dlq():
    store = InMemoryOutboxStore()
    msg = publish(store, target_url="http://kl/cb", payload={"x": 1})
    msg.max_attempts = 2

    def fail(url, method, headers, payload):
        return 503

    # 1차 실패
    deliver_once(store, http_send=fail)
    # next_retry_at이 미래로 잡혔으므로 2차 dequeue_ready에서 안 나옴 → 강제 리셋
    msg.next_retry_at = 0
    store.update(msg)
    deliver_once(store, http_send=fail)

    assert store.stats()["pending"] == 0
    assert store.stats()["dlq"] == 1


def test_deliver_with_exception():
    store = InMemoryOutboxStore()
    publish(store, target_url="http://kl/cb", payload={"x": 1})

    def boom(url, method, headers, payload):
        raise ConnectionError("network down")

    r = deliver_once(store, http_send=boom)
    assert r["failed"] == 1
