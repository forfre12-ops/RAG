"""IdempotencyMiddleware 자체의 회귀 — 종전 테스트 0건이던 구간.

기존 test_idempotency.py 는 **저장소**(services/idempotency)를, test_confirm_idempotency.py 는
**엔드포인트** 동작을 본다. 그 사이의 미들웨어 로직(메서드 필터 · 자격 네임스페이스 ·
replay/conflict 헤더 · 비-2xx 미저장 · 예외 시 락 해제 · 저장소 장애 시 통과)은 아무도 안 봤다.

여기서 지키려는 것 중 가장 무거운 것은 **자격 네임스페이스**다. 같은 Idempotency-Key 를 서로
다른 주체가 쓰면 남의 응답이 재현될 수 있는데, 미들웨어는 인증 의존성보다 먼저 실행돼
request.state.auth_* 를 못 본다. 그래서 헤더 자격을 해시해 키 prefix 로 쓴다 — 그 격리가
깨지면 응답 본문이 교차 유출된다.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse
from starlette.testclient import TestClient

from koipa.api._idempotency_mw import IdempotencyMiddleware


class FakeStore:
    """결정적 인메모리 저장소 — 실제 Redis 없이 미들웨어 분기를 전부 태운다."""

    def __init__(self) -> None:
        self.saved: dict[str, tuple[int, bytes, str]] = {}
        self.locks: set[str] = set()
        self.released: list[str] = []
        self.acquire_result: bool | None = None   # None = 정상 동작

    def get(self, key):
        return self.saved.get(key)

    def acquire(self, key) -> bool:
        if self.acquire_result is not None:
            return self.acquire_result
        if key in self.locks:
            return False
        self.locks.add(key)
        return True

    def store(self, key, resp) -> None:
        self.saved[key] = resp

    def release(self, key) -> None:
        self.locks.discard(key)
        self.released.append(key)


@pytest.fixture
def ctx(monkeypatch):
    """앱 + 미들웨어 + 가짜 저장소. calls 로 핸들러 실제 실행 횟수를 센다."""
    store = FakeStore()
    calls = {"n": 0}

    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware)

    @app.post("/echo")
    def echo():
        calls["n"] += 1
        return {"n": calls["n"]}

    @app.get("/read")
    def read():
        calls["n"] += 1
        return {"n": calls["n"]}

    @app.post("/fail")
    def fail():
        calls["n"] += 1
        return JSONResponse(status_code=500, content={"detail": "boom"})

    @app.post("/raise")
    def raise_():
        calls["n"] += 1
        raise RuntimeError("handler exploded")

    @app.post("/stream")
    def stream():
        calls["n"] += 1
        return PlainTextResponse("data: x\n\n", media_type="text/event-stream")

    monkeypatch.setattr(
        "koipa.services.idempotency.get_idempotency_store", lambda: store
    )
    return TestClient(app, raise_server_exceptions=False), store, calls


# ── 통과 경로 (기존 동작 무영향) ────────────────────────────────────────────────

def test_no_key_passes_through(ctx):
    client, store, calls = ctx
    assert client.post("/echo").status_code == 200
    assert client.post("/echo").status_code == 200
    assert calls["n"] == 2          # 매번 실행 — 멱등 처리 안 함
    assert store.saved == {}


def test_non_mutating_method_ignores_key(ctx):
    """GET 은 키가 있어도 통과 — 조회를 캐시해 버리면 최신값이 안 보인다."""
    client, store, calls = ctx
    h = {"Idempotency-Key": "k1"}
    assert client.get("/read", headers=h).json()["n"] == 1
    assert client.get("/read", headers=h).json()["n"] == 2
    assert store.saved == {}


# ── 저장·재현 ──────────────────────────────────────────────────────────────────

def test_first_call_stores_and_second_replays(ctx):
    client, store, calls = ctx
    h = {"Idempotency-Key": "k1", "X-API-Key": "cred-a"}

    first = client.post("/echo", headers=h)
    assert first.headers.get("X-Idempotent-Stored") == "true"
    assert first.json() == {"n": 1}

    second = client.post("/echo", headers=h)
    assert second.headers.get("X-Idempotent-Replay") == "true"
    assert second.json() == {"n": 1}       # 저장된 본문 그대로
    assert calls["n"] == 1                 # 핸들러는 한 번만 실행됐다


def test_in_flight_returns_409(ctx):
    client, store, calls = ctx
    store.acquire_result = False           # 다른 워커가 처리 중인 상황
    resp = client.post("/echo", headers={"Idempotency-Key": "k1"})
    assert resp.status_code == 409
    assert resp.headers.get("X-Idempotent-Conflict") == "true"
    assert calls["n"] == 0                 # 핸들러가 돌면 중복 처리다


# ── 자격 네임스페이스 (교차 주체 응답 유출 차단) ───────────────────────────────

def test_same_key_different_credential_does_not_replay(ctx):
    """같은 키라도 자격이 다르면 남의 응답이 재현되면 안 된다."""
    client, store, calls = ctx
    a = client.post("/echo", headers={"Idempotency-Key": "same", "X-API-Key": "cred-a"})
    b = client.post("/echo", headers={"Idempotency-Key": "same", "X-API-Key": "cred-b"})

    assert a.json() == {"n": 1}
    assert b.json() == {"n": 2}            # 재현이 아니라 새로 실행
    assert b.headers.get("X-Idempotent-Replay") is None
    assert calls["n"] == 2
    assert len(store.saved) == 2           # 네임스페이스가 갈렸다


def test_authorization_and_api_key_are_separate_namespaces(ctx):
    client, store, calls = ctx
    client.post("/echo", headers={"Idempotency-Key": "same", "Authorization": "Bearer t"})
    client.post("/echo", headers={"Idempotency-Key": "same", "X-API-Key": "t"})
    assert calls["n"] == 2

    # 원시 비밀이 키에 그대로 들어가면 안 된다(해시 prefix 만).
    assert all("Bearer t" not in k and "t:" != k[:2] for k in store.saved)


# ── 저장하지 않아야 하는 경우 ─────────────────────────────────────────────────

def test_non_2xx_is_not_stored_and_lock_released(ctx):
    """실패를 저장하면 재시도가 영원히 같은 오류를 되돌려받는다."""
    client, store, calls = ctx
    h = {"Idempotency-Key": "k1"}
    assert client.post("/fail", headers=h).status_code == 500
    assert store.saved == {}
    assert "k1" in store.released[0]

    assert client.post("/fail", headers=h).status_code == 500
    assert calls["n"] == 2                 # 재시도가 실제로 가능하다


def test_streaming_response_is_not_stored(ctx):
    """SSE 를 본문으로 빨아들이면 스트림이 깨진다."""
    client, store, calls = ctx
    resp = client.post("/stream", headers={"Idempotency-Key": "k1"})
    assert resp.status_code == 200
    assert store.saved == {}
    assert store.released


def test_handler_exception_releases_lock(ctx):
    """예외 시 락이 남으면 그 키는 영원히 409 가 된다."""
    client, store, calls = ctx
    client.post("/raise", headers={"Idempotency-Key": "k1"})
    assert store.released, "예외 경로에서 release 가 호출되지 않았다"
    assert store.locks == set()


# ── 저장소 장애 ────────────────────────────────────────────────────────────────

def test_store_error_disables_idempotency_but_serves_request(monkeypatch):
    """저장소가 죽어도 요청은 처리한다(가용성 우선) — 조용히 500 나면 안 된다."""
    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware)

    @app.post("/echo")
    def echo():
        return {"ok": True}

    def _boom():
        raise RuntimeError("redis down")

    monkeypatch.setattr("koipa.services.idempotency.get_idempotency_store", _boom)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post("/echo", headers={"Idempotency-Key": "k1"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert resp.headers.get("X-Idempotent-Stored") is None
