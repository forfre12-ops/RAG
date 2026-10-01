"""제공자 가용성 조회 — 콘솔이 "우리가 실제로 가진 것만" 보여주는 근거.

왜(2026-09-28). healthz.llm_providers_supported 는 스키마가 받는 값
9개(noop·anthropic·openai·google·gemini·local_openai·vllm·ollama·lm_studio)를 그대로
내려준다 — 실제로 이 서버가 그 provider 로 지금 생성할 수 있는지와 무관하다. 이 시험은
GET /synth/providers/status 가 "고를 수 있다"와 "가진 것"을 가른다는 것과, GET
/synth/playbook 이 지금 생성에 기본 적용되는 규칙(generation_playbook.py)을 그대로
노출한다는 것을 잠근다.
"""
from __future__ import annotations

import os

os.environ.setdefault("TESTING", "1")

import pytest

from koipa.services import synth_provider_status as sps


@pytest.fixture(autouse=True)
def _clear_probe_cache():
    sps._cache.clear()
    yield
    sps._cache.clear()


def test_noop_is_always_available():
    rows = {r["name"]: r for r in sps.provider_status()}
    assert rows["noop"]["available"] is True


def test_commercial_availability_follows_server_key(monkeypatch):
    """상용은 키가 없으면 "가진 것"이 아니다 — 화면에 이유가 있어야 한다."""
    monkeypatch.setattr(sps.settings, "anthropic_api_key", "", raising=False)
    monkeypatch.setattr(sps.settings, "openai_api_key", "sk-test", raising=False)
    rows = {r["name"]: r for r in sps.provider_status()}
    assert rows["anthropic"]["available"] is False
    assert "ANTHROPIC_API_KEY" in rows["anthropic"]["reason"]
    assert rows["openai"]["available"] is True
    assert rows["openai"]["reason"] == ""


def test_google_gemini_are_not_advertised():
    """상용은 GPT·Claude 둘만 advertise 한다(2026-09-28) — google/gemini 는 중복."""
    names = {r["name"] for r in sps.provider_status()}
    assert "google" not in names
    assert "gemini" not in names


def test_local_provider_unavailable_when_unreachable(monkeypatch):
    """연결이 안 되면 없다로 본다 — 실제 서버 유무를 재는 것이지 설정 유무가 아니다."""
    monkeypatch.setattr(sps, "_probe_http", lambda base_url: False)
    rows = {r["name"]: r for r in sps.provider_status()}
    for name in ("ollama", "vllm", "local_openai", "lm_studio"):
        assert rows[name]["available"] is False
        assert rows[name]["reason"], name


def test_local_provider_available_when_reachable(monkeypatch):
    monkeypatch.setattr(sps, "_probe_http", lambda base_url: True)
    rows = {r["name"]: r for r in sps.provider_status()}
    for name in ("ollama", "vllm", "local_openai", "lm_studio"):
        assert rows[name]["available"] is True


def test_probe_result_is_cached_within_ttl(monkeypatch):
    calls = []

    def _spy(base_url):
        calls.append(base_url)
        return True

    monkeypatch.setattr(sps, "_probe_http", _spy)
    sps.provider_status()
    sps.provider_status()
    # 4개 로컬 provider 를 두 번 조회했지만 실제 프로브는 한 번씩만 — TTL 캐시.
    assert len(calls) == 4, calls


# ── API 경로 ─────────────────────────────────────────────────────────────────


def test_providers_status_endpoint_answers():
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from koipa.api.app import app  # noqa: PLC0415

    with TestClient(app) as cli:
        res = cli.get(
            "/api/v1/synth/providers/status",
            headers={"X-API-Key": "test-key", "X-Actor-Role": "admin"},
        )
    if res.status_code == 404:
        pytest.skip("합성 라우터가 이 프로파일에서 꺼져 있다(enable_training=False)")
    assert res.status_code == 200, res.text
    body = res.json()
    names = {p["name"] for p in body["providers"]}
    assert names == {"noop", "anthropic", "openai", "ollama", "vllm", "local_openai", "lm_studio"}
    for p in body["providers"]:
        assert isinstance(p["available"], bool)


def test_playbook_endpoint_matches_the_generator_module():
    from fastapi.testclient import TestClient  # noqa: PLC0415

    from koipa.api.app import app  # noqa: PLC0415
    from koipa.modules.m1_synthesis.generation_playbook import (  # noqa: PLC0415
        playbook_text,
        playbook_version,
    )

    with TestClient(app) as cli:
        res = cli.get(
            "/api/v1/synth/playbook",
            headers={"X-API-Key": "test-key", "X-Actor-Role": "admin"},
        )
    if res.status_code == 404:
        pytest.skip("합성 라우터가 이 프로파일에서 꺼져 있다(enable_training=False)")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["version"] == playbook_version()
    assert body["text"] == playbook_text()
    assert len(body["rules"]) >= 1
    assert len(body["avoid_phrases"]) >= 1
