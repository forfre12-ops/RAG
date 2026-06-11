"""Track A (신뢰성) 회귀 테스트 — Wave3 감사.

대상 수정:
- M-outbox-inflight: InMemoryOutboxStore.dequeue_ready 가 in-flight(visibility
  timeout) 표시를 해서 두 번째 워커가 같은 메시지를 즉시 다시 못 집게.
- M-outbox-attempts: dequeue 후 deliver_once 의 attempts 증가가 update 로
  persist 되어 유실되지 않음.
- M-callback: callback_url 이 있으면 job 완료 시 outbox webhook 으로 publish
  (과거엔 _strip_async_fields 가 떼어내 webhook 이 영원히 안 울렸음).
- M-classify-tx: classification 영속화를 evidence 와 분리(별도 커밋)해
  evidence 실패가 분류 자체를 롤백하지 않음.

설계: PG·Redis 없이 동작. outbox InMemory + stub session 으로 검증.
"""

from __future__ import annotations

import time
import uuid

import pytest

from lloydk.services import outbox as outbox_mod
from lloydk.services.outbox import (
    InMemoryOutboxStore,
    deliver_once,
    publish,
)


# ---------------------------------------------------------------------------
# M-outbox-inflight + M-outbox-attempts
# ---------------------------------------------------------------------------


def test_inmemory_dequeue_sets_inflight_visibility():
    """두 번째 dequeue 는 같은 메시지를 즉시 다시 집지 못한다 (in-flight)."""
    store = InMemoryOutboxStore()
    publish(store, target_url="http://kl/cb", payload={"x": 1})

    first = store.dequeue_ready(limit=10)
    second = store.dequeue_ready(limit=10)

    assert len(first) == 1
    assert len(second) == 0, "in-flight 표시가 안 돼 두 번째 워커가 중복 처리"


def test_inmemory_inflight_visibility_window_is_future():
    """dequeue 된 메시지의 next_retry_at 은 미래(now+timeout)로 bump 된다."""
    store = InMemoryOutboxStore()
    msg = publish(store, target_url="http://kl/cb", payload={"x": 1})
    before = time.time()

    store.dequeue_ready(limit=10)

    # store 가 보관 중인 동일 메시지의 next_retry_at 확인
    stored = store._items[msg.id]  # noqa: SLF001
    assert stored.next_retry_at > before + store.VISIBILITY_TIMEOUT_SEC - 5


def test_inmemory_dequeue_returns_stored_reference_for_attempts_persist():
    """dequeue 가 store 보관 객체(참조)를 반환 → deliver_once 의 attempts 증가가
    update 로 persist 됨 (M-outbox-attempts).
    """
    store = InMemoryOutboxStore()
    msg = publish(store, target_url="http://kl/cb", payload={"x": 1})

    out = store.dequeue_ready(limit=10)
    assert out[0] is store._items[msg.id]  # noqa: SLF001


def test_attempts_persist_across_deliver_failures():
    """실패 시 deliver_once 의 attempts 증가가 store 에 보존된다."""
    store = InMemoryOutboxStore()
    msg = publish(store, target_url="http://kl/cb", payload={"x": 1})
    msg.max_attempts = 5

    def fail(url, method, headers, payload):
        return 500

    deliver_once(store, http_send=fail)
    assert store._items[msg.id].attempts == 1  # noqa: SLF001

    # next_retry_at 강제 리셋 후 재시도 → attempts 가 1에서 누적되어 2가 돼야 함
    reset = store._items[msg.id]  # noqa: SLF001
    reset.next_retry_at = 0
    store.update(reset)
    deliver_once(store, http_send=fail)
    assert store._items[msg.id].attempts == 2  # noqa: SLF001


def test_inflight_message_not_redelivered_within_window():
    """in-flight bump 로 인해 dequeue 직후 즉시 deliver_once 를 또 돌려도
    같은 메시지를 중복 전송하지 않는다.
    """
    store = InMemoryOutboxStore()
    publish(store, target_url="http://kl/cb", payload={"x": 1})

    calls = {"n": 0}

    def slow_ok(url, method, headers, payload):
        calls["n"] += 1
        return 200

    # 첫 dequeue 가 in-flight 표시 → 두 번째 deliver_once 는 ready 0건
    first = store.dequeue_ready(limit=10)
    assert len(first) == 1
    r2 = deliver_once(store, http_send=slow_ok)
    assert r2["ready"] == 0
    assert calls["n"] == 0


# ---------------------------------------------------------------------------
# M-callback
# ---------------------------------------------------------------------------


@pytest.fixture
def fresh_memory_outbox(monkeypatch):
    """get_outbox_store 싱글톤을 InMemory 로 강제 + 테스트 후 리셋."""
    outbox_mod.reset_default_store()
    monkeypatch.setenv("OUTBOX_BACKEND", "memory")
    yield
    outbox_mod.reset_default_store()


@pytest.fixture(autouse=True)
def _reset_classify_singleton():
    yield
    from lloydk.services.classify_service import ClassifyService
    ClassifyService._instance = None


@pytest.mark.slow
def test_callback_url_publishes_to_outbox_on_async_done(fresh_memory_outbox):
    from lloydk.schemas.classify_async import ClassifyAsyncRequest
    from lloydk.services.async_classify_service import AsyncClassifyService

    svc = AsyncClassifyService(sleep_fn=lambda _s: None)
    req = ClassifyAsyncRequest(
        doc_id="cb-1", content="샘플 영업비밀 내용", callback_url="http://kl/webhook",
    )
    res = svc.submit_async(req)

    store = outbox_mod.get_outbox_store()
    assert isinstance(store, InMemoryOutboxStore)
    pending = store.dequeue_ready(limit=10)
    assert len(pending) == 1, "callback_url 이 있는데 outbox 에 webhook 이 적재되지 않음"
    msg = pending[0]
    assert msg.target_url == "http://kl/webhook"
    assert msg.payload["job_id"] == str(res.job_id)
    assert msg.payload["status"] == "done"


@pytest.mark.slow
def test_no_callback_url_does_not_publish(fresh_memory_outbox):
    from lloydk.schemas.classify_async import ClassifyAsyncRequest
    from lloydk.services.async_classify_service import AsyncClassifyService

    svc = AsyncClassifyService(sleep_fn=lambda _s: None)
    svc.submit_async(ClassifyAsyncRequest(doc_id="cb-2", content="내용"))

    store = outbox_mod.get_outbox_store()
    assert store.dequeue_ready(limit=10) == []


@pytest.mark.slow
def test_callback_published_on_async_failure(fresh_memory_outbox):
    from lloydk.schemas.classify_async import ClassifyAsyncRequest
    from lloydk.services.async_classify_service import AsyncClassifyService

    svc = AsyncClassifyService(sleep_fn=lambda _s: None)

    def _boom(_req):
        raise RuntimeError("inference broke")

    svc.classify.classify = _boom  # type: ignore[attr-defined]
    res = svc.submit_async(
        ClassifyAsyncRequest(doc_id="cb-err", content="x", callback_url="http://kl/webhook")
    )

    store = outbox_mod.get_outbox_store()
    pending = store.dequeue_ready(limit=10)
    assert len(pending) == 1
    assert pending[0].payload["status"] == "failed"
    assert pending[0].payload["job_id"] == str(res.job_id)


@pytest.mark.slow
def test_callback_published_on_batch(fresh_memory_outbox):
    from lloydk.schemas.classify import ClassifyRequest
    from lloydk.schemas.classify_async import ClassifyBatchRequest
    from lloydk.services.async_classify_service import AsyncClassifyService

    svc = AsyncClassifyService(sleep_fn=lambda _s: None)
    req = ClassifyBatchRequest(
        documents=[ClassifyRequest(doc_id=f"bb{i}", content=f"내용 {i}") for i in range(2)],
        callback_url="http://kl/batch-webhook",
    )
    res = svc.submit_batch(req)

    store = outbox_mod.get_outbox_store()
    pending = store.dequeue_ready(limit=10)
    assert len(pending) == 1
    msg = pending[0]
    assert msg.target_url == "http://kl/batch-webhook"
    assert msg.payload["job_id"] == str(res.job_id)
    assert msg.payload["total"] == 2


def test_publish_callback_swallows_outbox_errors(monkeypatch):
    """outbox publish 실패가 호출자를 깨뜨리지 않는다 (best-effort)."""
    from lloydk.services.async_classify_service import AsyncClassifyService

    def _broken_publish(*a, **kw):
        raise RuntimeError("outbox down")

    monkeypatch.setattr(outbox_mod, "publish", _broken_publish)
    outbox_mod.reset_default_store()
    # 예외 없이 조용히 반환해야 한다
    AsyncClassifyService._publish_callback("http://kl/webhook", {"job_id": "x"})
    outbox_mod.reset_default_store()


# ---------------------------------------------------------------------------
# M-classify-tx — classification 과 evidence 의 트랜잭션 분리
# ---------------------------------------------------------------------------


class _RecordingClassifyRepo:
    """ClassifyRepo 의 _try_persist 가 호출하는 메서드만 stub.

    evidence 단계에서 예외를 던지도록 구성해, 그래도 classification_id 가
    보존되는지 검증.
    """

    created_classification_id = uuid.UUID("12345678-1234-5678-1234-567812345678")

    def __init__(self, db):
        self.db = db

    def tenant_exists(self, tenant_id):
        return True

    def document_exists(self, doc_id, tenant_id=None):
        return True

    def level_id_by_code(self, code):
        return 1

    def create_classification(self, **kw):
        class _Cls:
            classification_id = _RecordingClassifyRepo.created_classification_id
        return _Cls()

    def add_evidence_from_spans(self, *a, **kw):
        raise RuntimeError("evidence write exploded")

    def add_rag_evidence_from_hits(self, *a, **kw):
        raise RuntimeError("rag evidence write exploded")


@pytest.mark.slow
def test_evidence_failure_does_not_discard_classification(monkeypatch):
    """evidence 영속화가 터져도 classification_id 는 그대로 반환되고
    warning 만 추가된다 (M-classify-tx).
    """
    from contextlib import contextmanager

    from lloydk.schemas.classify import EvidenceSpan
    from lloydk.schemas.common import Grade
    from lloydk.services import classify_service as cs_mod
    from lloydk.services.classify_service import ClassifyService

    # session_scope 를 no-op stub 으로 — 실제 DB 불필요.
    @contextmanager
    def _stub_scope():
        yield object()

    import lloydk.db as db_mod
    monkeypatch.setattr(db_mod, "session_scope", _stub_scope)
    monkeypatch.setattr(cs_mod, "ClassifyRepo", _RecordingClassifyRepo)

    svc = ClassifyService.__new__(ClassifyService)  # __init__(모델 로드) 우회

    class _Pred:
        label = Grade.TS
        scores = {"TS": 1.0}
        confidence = 0.9
        model_version = "test"
        rag_context = []
        evidence = [EvidenceSpan(text="비밀 근거", start=0, end=4, weight=1.0, tag="keyword_match")]

    class _Req:
        doc_id = "12345678-1234-5678-1234-567812345678"
        tenant_id = "t-1"

    cid, warns = svc._try_persist(_Req(), _Pred(), chunks=[])  # noqa: SLF001

    assert cid == _RecordingClassifyRepo.created_classification_id, (
        "evidence 실패가 classification_id 를 폐기했다 — 트랜잭션 분리 실패"
    )
    assert any("evidence persist failed" in w for w in warns)


@pytest.mark.slow
def test_classification_returned_when_no_evidence(monkeypatch):
    """evidence/rag 가 없으면 Step 2 를 건너뛰고 classification_id 반환."""
    from contextlib import contextmanager

    from lloydk.schemas.common import Grade
    from lloydk.services import classify_service as cs_mod
    from lloydk.services.classify_service import ClassifyService

    @contextmanager
    def _stub_scope():
        yield object()

    import lloydk.db as db_mod
    monkeypatch.setattr(db_mod, "session_scope", _stub_scope)
    monkeypatch.setattr(cs_mod, "ClassifyRepo", _RecordingClassifyRepo)

    svc = ClassifyService.__new__(ClassifyService)

    class _Pred:
        label = Grade.TS
        scores = {"TS": 1.0}
        confidence = 0.9
        model_version = "test"
        rag_context = []
        evidence = []

    class _Req:
        doc_id = "12345678-1234-5678-1234-567812345678"
        tenant_id = "t-1"

    cid, warns = svc._try_persist(_Req(), _Pred(), chunks=[])  # noqa: SLF001
    assert cid == _RecordingClassifyRepo.created_classification_id
    assert not any("evidence persist failed" in w for w in warns)
