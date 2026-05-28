"""P1-E3: webhook outbox 패턴 검증 — InMemory + Redis 백엔드 동등성·DLQ stream."""

from __future__ import annotations

import importlib
import os
import time

import pytest

from lloydk.services.outbox import (
    InMemoryOutboxStore,
    RedisOutboxStore,
    _select_backend,
    deliver_once,
    get_outbox_store,
    publish,
    reset_default_store,
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


# ---------------------------------------------------------------------------
# Redis 백엔드 — fakeredis 우선, 실패시 skip
# ---------------------------------------------------------------------------


@pytest.fixture
def redis_outbox():
    try:
        import redis  # noqa: F401, PLC0415
    except ImportError:
        pytest.skip("redis 라이브러리 미설치")

    explicit_url = os.environ.get("REDIS_URL_TEST")
    if explicit_url:
        try:
            store = RedisOutboxStore(explicit_url)
        except Exception as exc:  # noqa: BLE001
            pytest.skip(f"REDIS_URL_TEST 가용성 검증 실패 — err={type(exc).__name__}")
        store._client.flushdb()  # noqa: SLF001
        yield store
        try:
            store._client.flushdb()  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass
        return

    try:
        fakeredis = importlib.import_module("fakeredis")
    except ImportError:
        fakeredis = None

    if fakeredis is not None:
        store = RedisOutboxStore.__new__(RedisOutboxStore)
        store._redis_module = importlib.import_module("redis")  # noqa: SLF001
        store._client = fakeredis.FakeStrictRedis(decode_responses=True)  # noqa: SLF001
        store._ttl = 60  # noqa: SLF001
        store._url = "fakeredis://"  # noqa: SLF001
        yield store
        return

    try:
        store = RedisOutboxStore("redis://localhost:6379/14")
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"redis 서버 미가용 (fakeredis도 없음) — err={type(exc).__name__}")
    store._client.flushdb()  # noqa: SLF001
    yield store
    try:
        store._client.flushdb()  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass


def test_redis_publish_then_dequeue(redis_outbox):
    publish(redis_outbox, target_url="http://kl/cb", payload={"x": 1})
    ready = redis_outbox.dequeue_ready(limit=10)
    assert len(ready) == 1
    assert ready[0].target_url == "http://kl/cb"
    assert ready[0].payload == {"x": 1}


def test_redis_success_removes_from_pending(redis_outbox):
    publish(redis_outbox, target_url="http://kl/cb", payload={"x": 1})
    deliver_once(redis_outbox, http_send=lambda *a, **kw: 200)
    assert redis_outbox.stats()["pending"] == 0


def test_redis_failure_reschedules(redis_outbox):
    publish(redis_outbox, target_url="http://kl/cb", payload={"x": 1})
    r = deliver_once(redis_outbox, http_send=lambda *a, **kw: 500)
    assert r["failed"] == 1
    # 백오프로 미래에 잡혀있어 즉시 dequeue 안 됨
    assert redis_outbox.dequeue_ready(limit=10) == []
    # 그러나 pending 카운트는 살아있음
    assert redis_outbox.stats()["pending"] == 1


def test_redis_max_attempts_to_dlq_stream(redis_outbox):
    msg = publish(redis_outbox, target_url="http://kl/cb", payload={"x": 1}, max_attempts=2)
    # 1차: 실패 → 백오프
    deliver_once(redis_outbox, http_send=lambda *a, **kw: 503)
    # next_retry_at 강제 리셋 후 즉시 재시도
    msg.next_retry_at = time.time()
    msg.attempts = 1  # update에서 그대로 반영 (실패 카운터는 deliver_once 내부에서 +1됨 → 총 2)
    redis_outbox.update(msg)
    deliver_once(redis_outbox, http_send=lambda *a, **kw: 503)

    stats = redis_outbox.stats()
    assert stats["pending"] == 0
    assert stats["dlq"] == 1


def test_redis_dequeue_visibility_lock(redis_outbox):
    """동일 메시지를 두 번 연속 dequeue → 두 번째는 비어야 한다 (in-flight)."""
    publish(redis_outbox, target_url="http://kl/cb", payload={"x": 1})
    first = redis_outbox.dequeue_ready(limit=10)
    second = redis_outbox.dequeue_ready(limit=10)
    assert len(first) == 1
    assert len(second) == 0


# ---------------------------------------------------------------------------
# 팩토리
# ---------------------------------------------------------------------------


def test_select_backend_priorities():
    assert _select_backend("redis://x", None) == "redis"
    assert _select_backend(None, None) == "memory"
    assert _select_backend("redis://x", "memory") == "memory"
    assert _select_backend(None, "redis") == "redis"
    assert _select_backend("redis://x", "weird") == "memory"


def test_get_outbox_store_memory_explicit():
    reset_default_store()
    s = get_outbox_store(redis_url="redis://localhost:6379/0", env_backend="memory")
    assert isinstance(s, InMemoryOutboxStore)
    reset_default_store()


def test_get_outbox_store_redis_unreachable_falls_back():
    reset_default_store()
    s = get_outbox_store(redis_url="redis://127.0.0.1:1/0", env_backend="redis")
    assert isinstance(s, InMemoryOutboxStore)
    reset_default_store()
