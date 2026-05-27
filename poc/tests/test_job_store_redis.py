"""JobStore Redis 백엔드 + InMemory 동작 동등성·동시성 회귀 테스트.

Redis 서버 미가용 시 redis_store fixture가 skip 처리. CI/dryrun 환경에서는
in-memory 케이스만 실행되고 redis 케이스는 자동 skip.
"""

from __future__ import annotations

import os
import threading
import uuid

import pytest

from lloydk.services.job_store import (
    InMemoryJobStore,
    JobStore,
    RedisJobStore,
    _select_backend,
    get_default_store,
    get_job_store,
    reset_default_store,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def memory_store() -> InMemoryJobStore:
    return InMemoryJobStore()


@pytest.fixture
def redis_store():
    """RedisJobStore 인스턴스.

    우선순위:
    1. ``REDIS_URL_TEST`` env로 실 Redis 지정 → 사용 (CI integration).
    2. ``fakeredis`` 가용 → in-process fake Redis (CI default).
    3. localhost:6379 가용 → 사용.
    4. 위 모두 실패 → skip.
    """
    try:
        import redis  # noqa: F401, PLC0415
    except ImportError:
        pytest.skip("redis 라이브러리 미설치 — redis 백엔드 테스트 skip")

    explicit_url = os.environ.get("REDIS_URL_TEST")
    if explicit_url:
        try:
            store = RedisJobStore(explicit_url, ttl_seconds=60)
        except Exception as exc:  # noqa: BLE001
            pytest.skip("REDIS_URL_TEST 가용성 검증 실패 — skip (err=%s)" % type(exc).__name__)
        store._client.flushdb()  # noqa: SLF001
        yield store
        try:
            store._client.flushdb()  # noqa: SLF001
        except Exception:  # noqa: BLE001
            pass
        return

    # fakeredis 우선 — CI/로컬 모두에서 실 Redis 서버 없이 검증.
    # importlib로 동적 로드 (정적 분석에서 미설치 환경 false-positive 차단).
    import importlib  # noqa: PLC0415

    try:
        fakeredis = importlib.import_module("fakeredis")
    except ImportError:
        fakeredis = None

    if fakeredis is not None:
        store = RedisJobStore.__new__(RedisJobStore)
        store._redis_module = importlib.import_module("redis")  # noqa: SLF001
        store._client = fakeredis.FakeStrictRedis(decode_responses=True)  # noqa: SLF001
        store._ttl = 60  # noqa: SLF001
        store._url = "fakeredis://"  # noqa: SLF001
        yield store
        return

    # 마지막 폴백: localhost
    try:
        store = RedisJobStore("redis://localhost:6379/15", ttl_seconds=60)
    except Exception as exc:  # noqa: BLE001
        pytest.skip("redis 서버 미가용 (fakeredis도 없음) — skip (err=%s)" % type(exc).__name__)
    store._client.flushdb()  # noqa: SLF001
    yield store
    try:
        store._client.flushdb()  # noqa: SLF001
    except Exception:  # noqa: BLE001
        pass


# 두 백엔드 parametrize: memory 케이스는 redis_store fixture를 끌어오지 않도록
# 분기. 그렇지 않으면 redis 미가용 시 memory 케이스까지 skip됨.
@pytest.fixture
def store(request):
    if request.param == "memory":
        return InMemoryJobStore()
    # redis_store fixture를 명시적으로 요청 — skip 트리거는 fixture 내부에서
    return request.getfixturevalue("redis_store")


# ---------------------------------------------------------------------------
# 인터페이스 동등성
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("store", ["memory", "redis"], indirect=True)
def test_create_then_get_returns_payload(store):
    job_id = uuid.uuid4()
    store.create(job_id, payload={"total": 3, "completed": 0})

    doc = store.get(job_id)
    assert doc is not None
    assert doc["status"] == "queued"
    assert doc["total"] == 3
    assert doc["completed"] == 0


@pytest.mark.parametrize("store", ["memory", "redis"], indirect=True)
def test_update_merges_fields(store):
    job_id = uuid.uuid4()
    store.create(job_id, payload={"total": 5, "completed": 0})
    store.update(job_id, completed=2)
    store.update(job_id, status="done", results=[{"a": 1}])

    doc = store.get(job_id)
    assert doc["completed"] == 2
    assert doc["status"] == "done"
    assert doc["results"] == [{"a": 1}]
    assert doc["total"] == 5  # 기존 필드 보존


@pytest.mark.parametrize("store", ["memory", "redis"], indirect=True)
def test_get_unknown_job_returns_none(store):
    assert store.get(uuid.uuid4()) is None


@pytest.mark.parametrize("store", ["memory", "redis"], indirect=True)
def test_update_unknown_job_no_raise(store):
    """unknown job update는 warning 로깅 + 무시 (예외 던지지 않음)."""
    store.update(uuid.uuid4(), status="done")  # raise 없으면 통과


@pytest.mark.parametrize("store", ["memory", "redis"], indirect=True)
def test_set_status_shortcut(store):
    job_id = uuid.uuid4()
    store.create(job_id, payload={"total": 1})
    store.set_status(job_id, "running")
    assert store.get(job_id)["status"] == "running"


@pytest.mark.parametrize("store", ["memory", "redis"], indirect=True)
def test_list_recent_returns_inserted(store):
    ids = [uuid.uuid4() for _ in range(3)]
    for j in ids:
        store.create(j, payload={"total": 1})

    items = store.list_recent(limit=10)
    assert len(items) >= 3
    statuses = {it["status"] for it in items}
    assert "queued" in statuses


# ---------------------------------------------------------------------------
# 동시성 — 2개 스레드가 동시에 set_status 호출
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("store", ["memory", "redis"], indirect=True)
def test_concurrent_set_status_no_loss(store):
    """동시에 다른 필드 update — 최종 상태에서 양쪽 fields 모두 보존돼야 함.

    last-writer-wins 단순 GET/SET이면 한쪽 필드가 유실됨. InMemoryJobStore는
    Lock으로, RedisJobStore는 WATCH/MULTI/EXEC + 재시도로 보호.
    """
    job_id = uuid.uuid4()
    store.create(job_id, payload={"total": 2, "completed": 0})

    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def set_completed():
        try:
            barrier.wait(timeout=2)
            store.update(job_id, completed=1)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    def set_status_done():
        try:
            barrier.wait(timeout=2)
            store.update(job_id, status="done")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=set_completed)
    t2 = threading.Thread(target=set_status_done)
    t1.start()
    t2.start()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not errors, "동시 update 중 예외 발생: %s" % errors

    doc = store.get(job_id)
    # 두 update가 모두 머지됐어야 함 — race-free 검증
    assert doc["completed"] == 1
    assert doc["status"] == "done"
    assert doc["total"] == 2  # 초기 payload 보존


# ---------------------------------------------------------------------------
# 팩토리 / 폴백
# ---------------------------------------------------------------------------


def test_jobstore_alias_is_inmemory():
    """기존 ``JobStore`` 이름은 InMemoryJobStore alias여야 한다 (하위 호환)."""
    assert JobStore is InMemoryJobStore


def test_select_backend_env_override():
    assert _select_backend("redis://x", "memory") == "memory"
    assert _select_backend(None, "redis") == "redis"
    assert _select_backend("redis://x", None) == "redis"
    assert _select_backend(None, None) == "memory"
    # 알 수 없는 값은 memory fallback
    assert _select_backend("redis://x", "weird") == "memory"


def test_get_job_store_memory_explicit():
    s = get_job_store(redis_url="redis://localhost:6379/0", env_backend="memory")
    assert isinstance(s, InMemoryJobStore)


def test_get_job_store_redis_unreachable_falls_back_to_memory():
    """포트 1로 강제 거부 — RedisJobStore 생성 실패 → InMemoryJobStore 폴백."""
    s = get_job_store(redis_url="redis://127.0.0.1:1/0", env_backend="redis")
    assert isinstance(s, InMemoryJobStore)


def test_get_default_store_singleton():
    reset_default_store()
    s1 = get_default_store()
    s2 = get_default_store()
    assert s1 is s2
    reset_default_store()
