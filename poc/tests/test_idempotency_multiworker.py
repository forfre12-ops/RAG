"""[정합성] 멀티워커 idempotency 안전성 + 폴백 가시화.

redis 미가용 시 idempotency가 프로세스-로컬 _MemoryStore로 무음 폴백한다. 멀티워커
(WEB_CONCURRENCY>1)에선 워커 간 멱등성이 깨져 변경성 POST 재시도가 중복 처리될 수 있다.
폴백을 메트릭으로 노출하고, 멀티워커+메모리 조합은 운영 startup에서 fail-fast.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from koipa.api import prom_metrics as pm
from koipa.services import idempotency as idem
from koipa.services.idempotency import (
    _MemoryStore,
    assert_multiworker_idempotency_safe,
    get_idempotency_store,
    reset_idempotency_store_for_test,
)


def _fb_val() -> float:
    return pm.IDEMPOTENCY_BACKEND_FALLBACK_TOTAL._value.get()


# ── 멀티워커 fail-fast ────────────────────────────────────────────────────────

def test_single_worker_no_op(monkeypatch):
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    reset_idempotency_store_for_test()
    idem._store = _MemoryStore()  # 메모리 폴백이라도 1워커면 정확
    assert_multiworker_idempotency_safe()  # 예외 없음


def test_multiworker_memory_fails_fast(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    reset_idempotency_store_for_test()
    idem._store = _MemoryStore()
    with pytest.raises(RuntimeError, match="멀티워커"):
        assert_multiworker_idempotency_safe()


def test_multiworker_with_nonmemory_store_ok(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    reset_idempotency_store_for_test()
    idem._store = SimpleNamespace()  # redis-like(비 _MemoryStore) → 안전
    assert_multiworker_idempotency_safe()  # 예외 없음


def test_invalid_web_concurrency_treated_as_one(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "not-a-number")
    reset_idempotency_store_for_test()
    idem._store = _MemoryStore()
    assert_multiworker_idempotency_safe()  # 파싱 실패 → 1 → no-op


# ── 폴백 메트릭 ───────────────────────────────────────────────────────────────

def test_fallback_increments_metric(monkeypatch):
    from koipa import config as cfg
    # redis를 도달 불가 포트로 → ping 즉시 거부 → 메모리 폴백.
    monkeypatch.setattr(cfg.settings, "redis_url", "redis://127.0.0.1:1/0", raising=False)
    reset_idempotency_store_for_test()
    before = _fb_val()
    store = get_idempotency_store()
    assert isinstance(store, _MemoryStore)
    assert _fb_val() == before + 1


def teardown_function(_):
    reset_idempotency_store_for_test()
