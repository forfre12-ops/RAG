"""비동기 발사 가능 판정(_celery_dispatch_available)의 결과 저장 — PER-002 "등록 3초 이내".

종전에는 비동기 요청마다 브로커에 TCP 연결을 새로 시도해, 브로커가 죽어 있으면 **요청마다**
0.5초(localhost 두 주소면 약 1초)를 기다렸다. 이제 결과를 _DISPATCH_TTL_S 동안 재사용하고,
발사가 실패하면 즉시 버린다.

⚠ 판정은 시험 환경 표지(TESTING · PYTEST_CURRENT_TEST)가 있으면 저장 이전에 False 로 끝난다.
그래서 저장 경로를 재는 시험은 본문 안에서 두 표지를 지운다 — pytest 는 단계(setup·call)마다
PYTEST_CURRENT_TEST 를 다시 넣으므로 fixture 에서 지우면 본문에서 되살아난다.
"""
from __future__ import annotations

import socket

import pytest

import koipa.services.async_classify_service as acs


@pytest.fixture(autouse=True)
def _fresh_cache():
    acs.invalidate_dispatch_cache()
    yield
    acs.invalidate_dispatch_cache()


def _leave_test_env(monkeypatch) -> None:
    monkeypatch.delenv("TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)


def _count_connect(monkeypatch, *, ok: bool) -> dict:
    calls = {"n": 0}

    class _Sock:
        def close(self) -> None:
            pass

    def _fake(addr, timeout=None):  # noqa: ARG001 — socket.create_connection 시그니처
        calls["n"] += 1
        if not ok:
            raise OSError("connection refused")
        return _Sock()

    monkeypatch.setattr(socket, "create_connection", _fake)
    return calls


def test_available_result_is_reused_within_ttl(monkeypatch):
    calls = _count_connect(monkeypatch, ok=True)
    _leave_test_env(monkeypatch)
    assert acs._celery_dispatch_available() is True
    assert acs._celery_dispatch_available() is True
    assert calls["n"] == 1, "TTL 안에서는 브로커에 다시 붙지 않아야 한다"


def test_unavailable_result_is_reused_within_ttl(monkeypatch):
    """핵심: 브로커가 죽어 있어도 요청마다 기다리지 않는다."""
    calls = _count_connect(monkeypatch, ok=False)
    _leave_test_env(monkeypatch)
    assert acs._celery_dispatch_available() is False
    assert acs._celery_dispatch_available() is False
    assert calls["n"] == 1


def test_expired_ttl_probes_again(monkeypatch):
    calls = _count_connect(monkeypatch, ok=True)
    monkeypatch.setattr(acs, "_DISPATCH_TTL_S", 0.0)
    _leave_test_env(monkeypatch)
    acs._celery_dispatch_available()
    acs._celery_dispatch_available()
    assert calls["n"] == 2


def test_invalidate_forces_a_new_probe(monkeypatch):
    calls = _count_connect(monkeypatch, ok=True)
    _leave_test_env(monkeypatch)
    acs._celery_dispatch_available()
    acs.invalidate_dispatch_cache()
    acs._celery_dispatch_available()
    assert calls["n"] == 2


def test_test_environment_short_circuits_before_cache(monkeypatch):
    calls = _count_connect(monkeypatch, ok=True)
    monkeypatch.setenv("TESTING", "1")
    assert acs._celery_dispatch_available() is False
    assert calls["n"] == 0


def test_enqueue_failure_drops_saved_result(monkeypatch):
    """저장된 '가용'으로 발사했는데 실패하면 값을 버리고 in-process 로 처리한다."""
    import koipa.workers.tasks as tasks

    class _Broken:
        @staticmethod
        def delay(*_a, **_k):
            raise ConnectionError("broker went away")

    dropped = {"n": 0}
    monkeypatch.setattr(acs, "_celery_dispatch_available", lambda: True)
    monkeypatch.setattr(acs, "invalidate_dispatch_cache", lambda: dropped.__setitem__("n", dropped["n"] + 1))
    monkeypatch.setattr(tasks, "classify_async", _Broken)

    from koipa.schemas.classify_async import ClassifyAsyncRequest
    from koipa.services.classify_service import ClassifyService

    try:
        svc = acs.AsyncClassifyService(sleep_fn=lambda _s: None)
        res = svc.submit_async(ClassifyAsyncRequest(doc_id="probe-1", content="샘플 내용 영업비밀"))
    finally:
        ClassifyService._instance = None
    assert res.status == "done", "발사 실패는 in-process 폴백으로 처리돼야 한다"
    assert dropped["n"] == 1
