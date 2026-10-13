"""상용 LLM(anthropic·openai) 콘솔 키 설정 — 2026-10-11 사용자 명시적 지시로 신설.

전까지는 "콘솔에 키 입력칸을 만들지 않는다"가 확정 정책이었다
([[no-credential-prompt-ever-2026-08-24]]) — 이 경로(LLM 공급자 키)에 한해 뒤집혔다
([[llm-credential-console-input-reversal-2026-10-11]]). 이 시험이 잠그는 것:
① 조회 API는 절대 평문을 돌려주지 않는다(마지막 4자 마스킹만).
② 콘솔에서 설정한 키가 build_provider()가 실제로 쓰는 키다(.env보다 우선).
③ 쓰기(PUT/DELETE)는 admin 전용 — 다른 역할은 403.
"""

from __future__ import annotations

import os

os.environ.setdefault("TESTING", "1")

import pytest

from koipa.services import llm_credential_store as store


class _FakeRedis:
    """redis.Redis 최소 흉내 — get/set/delete만. 실제 redis 연결 없이 로직만 검증한다."""

    def __init__(self):
        self._data: dict[str, str] = {}

    def get(self, key):
        return self._data.get(key)

    def set(self, key, value):
        self._data[key] = value

    def delete(self, *keys):
        for k in keys:
            self._data.pop(k, None)


@pytest.fixture
def fake_redis(monkeypatch):
    fr = _FakeRedis()
    monkeypatch.setattr(store, "_client", lambda: fr)
    return fr


# ── 서비스 계층 ──────────────────────────────────────────────────────────────


def test_unset_provider_falls_back_to_env_default(fake_redis):
    assert store.get_credential("anthropic", default="env-value") == "env-value"


def test_console_value_overrides_env_default(fake_redis):
    store.set_credential("sk-ant-console123", provider="anthropic", actor_id="u1", actor_role="admin")
    assert store.get_credential("anthropic", default="env-value") == "sk-ant-console123"


def test_clear_restores_env_default(fake_redis):
    store.set_credential("sk-ant-console123", provider="anthropic", actor_id="u1", actor_role="admin")
    store.clear_credential("anthropic", actor_id="u1", actor_role="admin")
    assert store.get_credential("anthropic", default="env-value") == "env-value"


def test_set_rejects_unsupported_provider(fake_redis):
    with pytest.raises(ValueError):
        store.set_credential("x", provider="google", actor_id="u1", actor_role="admin")


def test_set_rejects_empty_value(fake_redis):
    with pytest.raises(ValueError):
        store.set_credential("   ", provider="anthropic", actor_id="u1", actor_role="admin")


def test_status_never_contains_the_raw_value(fake_redis):
    store.set_credential("sk-ant-abcDEF123456", provider="anthropic", actor_id="u1", actor_role="admin")
    s = store.status("anthropic", env_default="")
    assert s["configured"] is True
    assert s["source"] == "console"
    assert "sk-ant-abcDEF123456" not in s["masked"]
    assert s["masked"].endswith("3456")


def test_status_reports_env_source_when_console_unset(fake_redis):
    s = store.status("anthropic", env_default="sk-ant-from-env")
    assert s["configured"] is True
    assert s["source"] == "env"
    assert "sk-ant-from-env" not in s["masked"]


def test_status_reports_unconfigured_when_neither_set(fake_redis):
    s = store.status("anthropic", env_default="")
    assert s == {"configured": False, "source": "none", "masked": ""}


def test_redis_failure_falls_back_to_env_default(monkeypatch):
    def _boom():
        raise ConnectionError("no redis")

    monkeypatch.setattr(store, "_client", _boom)
    assert store.get_credential("anthropic", default="env-value") == "env-value"
    s = store.status("anthropic", env_default="env-value")
    assert s["source"] == "env"


# ── build_provider() 배선 ───────────────────────────────────────────────────


def test_build_provider_uses_console_key_over_env(fake_redis, monkeypatch):
    """build_provider()가 AnthropicProvider(api_key=...)에 콘솔 키를 넘기는지만 본다 —
    anthropic SDK(네트워크 클라이언트) 자체는 이 환경에 안 깔려 있을 수 있어(9/25 실측 이력)
    건드리지 않는다."""
    import koipa.adapters.llm.anthropic_provider as anthropic_mod
    from koipa.adapters.llm import build_provider
    from koipa.config import settings

    monkeypatch.setattr(settings, "anthropic_api_key", "env-key", raising=False)
    monkeypatch.setattr(settings, "llm_provider", "anthropic", raising=False)
    store.set_credential("console-key", provider="anthropic", actor_id="u1", actor_role="admin")

    captured = {}

    class _FakeAnthropicProvider:
        def __init__(self, *, model=None, api_key=None):
            captured["api_key"] = api_key

    monkeypatch.setattr(anthropic_mod, "AnthropicProvider", _FakeAnthropicProvider)
    build_provider("anthropic")
    assert captured["api_key"] == "console-key"


# ── API 경로 ─────────────────────────────────────────────────────────────────

API = "/api/v1"


def _hdr(role: str):
    from koipa.config import settings

    return {"X-API-Key": settings.api_key or "test-key", "X-Actor-Id": "cred-tester", "X-Actor-Role": role}


@pytest.fixture
def client(fake_redis):
    from fastapi.testclient import TestClient

    from koipa.api.app import app

    with TestClient(app) as c:
        yield c


def test_non_admin_cannot_set_credential(client):
    res = client.put(
        f"{API}/synth/providers/anthropic/credential", headers=_hdr("reviewer"), json={"api_key": "sk-ant-x"}
    )
    if res.status_code == 404:
        pytest.skip("합성 라우터가 이 프로파일에서 꺼져 있다(enable_training=False)")
    assert res.status_code == 403, res.text


def test_admin_can_set_and_response_never_echoes_raw_value(client):
    res = client.put(
        f"{API}/synth/providers/anthropic/credential", headers=_hdr("admin"), json={"api_key": "sk-ant-secretvalue1234"}
    )
    if res.status_code == 404:
        pytest.skip("합성 라우터가 이 프로파일에서 꺼져 있다(enable_training=False)")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["configured"] is True
    assert body["source"] == "console"
    assert "sk-ant-secretvalue1234" not in res.text


def test_unsupported_provider_is_404(client):
    res = client.put(f"{API}/synth/providers/google/credential", headers=_hdr("admin"), json={"api_key": "x"})
    if res.status_code == 404 and res.json().get("detail") == "Not Found":
        pytest.skip("합성 라우터가 이 프로파일에서 꺼져 있다(enable_training=False)")
    assert res.status_code == 404
    assert "google" in res.json()["detail"]


def test_delete_clears_and_reflects_in_provider_status(client):
    client.put(f"{API}/synth/providers/anthropic/credential", headers=_hdr("admin"), json={"api_key": "sk-ant-x"})
    res = client.delete(f"{API}/synth/providers/anthropic/credential", headers=_hdr("admin"))
    if res.status_code == 404:
        pytest.skip("합성 라우터가 이 프로파일에서 꺼져 있다(enable_training=False)")
    assert res.status_code == 200, res.text
    assert res.json()["configured"] in (False, True)  # env 기본값이 있으면 True(source=env)로 복귀

    status_res = client.get(f"{API}/synth/providers/status", headers=_hdr("admin"))
    rows = {r["name"]: r for r in status_res.json()["providers"]}
    assert rows["anthropic"]["credential_source"] != "console"
