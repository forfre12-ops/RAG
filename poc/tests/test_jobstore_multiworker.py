"""[정합성] 멀티워커 async job 저장소 안전성 — idempotency 가드와 동형.

redis 미가용 시 job_store가 프로세스-로컬 InMemoryJobStore로 무음 폴백한다. 멀티워커
(WEB_CONCURRENCY>1)에선 워커 A에 제출한 job을 워커 B가 조회하지 못해 유실된다.
멀티워커+메모리 조합은 운영 startup에서 fail-fast(단일 워커/파싱실패는 no-op).
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from lloydk.services import job_store as js
from lloydk.services.job_store import (
    InMemoryJobStore,
    assert_multiworker_jobstore_safe,
)


def test_single_worker_no_op(monkeypatch):
    monkeypatch.delenv("WEB_CONCURRENCY", raising=False)
    monkeypatch.setenv("JOB_STORE_BACKEND", "memory")  # 메모리라도 1워커면 조회 일관
    assert_multiworker_jobstore_safe()  # 예외 없음


def test_multiworker_memory_fails_fast(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    monkeypatch.setenv("JOB_STORE_BACKEND", "memory")  # → InMemoryJobStore
    with pytest.raises(RuntimeError, match="멀티워커"):
        assert_multiworker_jobstore_safe()


def test_multiworker_with_nonmemory_store_ok(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "2")
    # redis-like(비 InMemoryJobStore) 반환 → 안전(연결 불필요, 팩토리만 대체)
    monkeypatch.setattr(js, "get_job_store", lambda: SimpleNamespace())
    assert_multiworker_jobstore_safe()  # 예외 없음


def test_invalid_web_concurrency_treated_as_one(monkeypatch):
    monkeypatch.setenv("WEB_CONCURRENCY", "not-a-number")
    monkeypatch.setenv("JOB_STORE_BACKEND", "memory")
    assert_multiworker_jobstore_safe()  # 파싱 실패 → 1 → no-op


def test_returns_inmemory_when_backend_memory(monkeypatch):
    # 가드가 검사하는 폴백 판정 경로 자체를 고정 — memory 백엔드는 InMemoryJobStore.
    monkeypatch.setenv("JOB_STORE_BACKEND", "memory")
    assert isinstance(js.get_job_store(), InMemoryJobStore)
