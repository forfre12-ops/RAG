"""P1-E3: webhook outbox 패턴 검증 — InMemory + Redis 백엔드 동등성·DLQ stream."""

from __future__ import annotations

import importlib
import json
import os
import time

import pytest

from koipa.config import settings
from koipa.services.outbox import (
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
# [2026-10-06] publish_callback 즉시 배송 kick — KL 요청(완료 즉시 발송, 60초는 재시도만)
# ---------------------------------------------------------------------------


def test_publish_callback_kicks_immediate_delivery_when_broker_available(monkeypatch):
    from koipa.services import outbox as outbox_mod

    reset_default_store()
    monkeypatch.setenv("OUTBOX_BACKEND", "memory")
    monkeypatch.setattr(
        "koipa.services.async_classify_service._celery_dispatch_available", lambda: True,
    )
    calls: list[tuple] = []

    class _FakeTask:
        def delay(self, *a, **kw):
            calls.append((a, kw))

    monkeypatch.setattr("koipa.workers.tasks.deliver_outbox_tick", _FakeTask())

    ok = outbox_mod.publish_callback("http://kl/cb", {"job_id": "j1", "status": "done"})
    assert ok is True
    assert len(calls) == 1, "브로커 가용이면 즉시 1회 배송 시도를 Celery 에 맡겨야 한다"
    reset_default_store()


def test_publish_callback_skips_kick_when_broker_unavailable(monkeypatch):
    from koipa.services import outbox as outbox_mod

    reset_default_store()
    monkeypatch.setenv("OUTBOX_BACKEND", "memory")
    monkeypatch.setattr(
        "koipa.services.async_classify_service._celery_dispatch_available", lambda: False,
    )
    calls: list[tuple] = []

    class _FakeTask:
        def delay(self, *a, **kw):
            calls.append((a, kw))

    monkeypatch.setattr("koipa.workers.tasks.deliver_outbox_tick", _FakeTask())

    ok = outbox_mod.publish_callback("http://kl/cb", {"job_id": "j1", "status": "done"})
    assert ok is True, "브로커 미가용이어도 outbox 적재 자체는 성공해야 한다(60초 틱이 나중에 집음)"
    assert len(calls) == 0
    reset_default_store()


def test_publish_callback_no_url_is_noop():
    from koipa.services import outbox as outbox_mod

    assert outbox_mod.publish_callback(None, {"job_id": "j1"}) is False


# ---------------------------------------------------------------------------
# [2026-10-06] publish_kl_stream — KL Consumer Group 직접구독용 Stream (사용자 결정)
# ---------------------------------------------------------------------------


def test_publish_kl_stream_noop_when_url_unset(monkeypatch):
    from koipa.services import outbox as outbox_mod

    monkeypatch.setattr(settings, "kl_stream_redis_url", "")
    assert outbox_mod.publish_kl_stream({"job_id": "j1", "status": "done"}) is False


def test_publish_kl_stream_xadds_with_configured_name_and_maxlen(monkeypatch):
    from koipa.services import outbox as outbox_mod

    outbox_mod.reset_kl_stream_client()
    monkeypatch.setattr(settings, "kl_stream_redis_url", "redis://kl-stream-host:6380/0")
    monkeypatch.setattr(settings, "kl_stream_name", "koipa:classify:results")
    monkeypatch.setattr(settings, "kl_stream_maxlen", 123)

    calls: list[dict] = []

    class _FakeClient:
        def xadd(self, name, entry, maxlen=None, approximate=None):
            calls.append({"name": name, "entry": entry, "maxlen": maxlen, "approximate": approximate})

    monkeypatch.setattr(outbox_mod, "_get_kl_stream_client", lambda url: _FakeClient())

    payload = {"job_id": "j1", "status": "done", "client_request_id": "req-9"}
    ok = outbox_mod.publish_kl_stream(payload)

    assert ok is True
    assert len(calls) == 1
    assert calls[0]["name"] == "koipa:classify:results"
    assert calls[0]["maxlen"] == 123
    assert calls[0]["approximate"] is True
    assert json.loads(calls[0]["entry"]["payload_json"]) == payload
    outbox_mod.reset_kl_stream_client()


def test_publish_kl_stream_failure_is_non_critical(monkeypatch):
    from koipa.services import outbox as outbox_mod

    outbox_mod.reset_kl_stream_client()
    monkeypatch.setattr(settings, "kl_stream_redis_url", "redis://kl-stream-host:6380/0")

    def _boom(url):
        raise ConnectionError("down")

    monkeypatch.setattr(outbox_mod, "_get_kl_stream_client", _boom)

    assert outbox_mod.publish_kl_stream({"job_id": "j1"}) is False
    outbox_mod.reset_kl_stream_client()


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
