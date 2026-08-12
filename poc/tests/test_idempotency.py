"""멱등성 저장소 단위 테스트 — in-memory 폴백 동작(락·재현·만료).

미들웨어 통합(같은 Idempotency-Key 재요청 → 동일 응답 재현)은 라이브 검증으로 확인.
여기서는 redis 비의존 폴백 저장소의 상태 모델을 검증한다.
"""

from __future__ import annotations

import time

import pytest

from koipa.services.idempotency import _MemoryStore


def test_acquire_then_store_then_get():
    s = _MemoryStore()
    key = "k1"
    # 첫 acquire 성공(in-flight)
    assert s.acquire(key) is True
    # 처리 중엔 결과 없음
    assert s.get(key) is None
    # 완료 응답 저장
    resp = (200, b'{"ok":true}', "application/json")
    s.store(key, resp)
    # 재현
    assert s.get(key) == resp


def test_second_acquire_blocked_while_in_flight():
    s = _MemoryStore()
    key = "k2"
    assert s.acquire(key) is True
    # 처리 중 동일 키 재요청 → 차단(409 유발)
    assert s.acquire(key) is False


def test_acquire_blocked_after_stored():
    s = _MemoryStore()
    key = "k3"
    s.acquire(key)
    s.store(key, (201, b"x", "application/json"))
    # 완료된 키는 재처리 금지(재현 경로로 가야 함)
    assert s.acquire(key) is False


def test_release_allows_retry():
    s = _MemoryStore()
    key = "k4"
    assert s.acquire(key) is True
    s.release(key)
    # 실패 후 락 해제 → 재시도 허용
    assert s.acquire(key) is True


def test_lock_expiry_allows_retry(monkeypatch):
    import koipa.services.idempotency as idem
    monkeypatch.setattr(idem, "_LOCK_TTL", 0)  # 즉시 만료
    s = _MemoryStore()
    key = "k5"
    assert s.acquire(key) is True
    time.sleep(0.01)
    # 락 TTL 만료 → 재획득 가능(처리 지연/크래시 복구)
    assert s.acquire(key) is True
