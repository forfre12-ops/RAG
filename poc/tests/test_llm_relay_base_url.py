# -*- coding: utf-8 -*-
"""상용 LLM 호출 주소 — 네트워크 중계서버(요구사항 ECR-001-06) 경유.

설정이 비어 있으면 종전과 똑같이 키만 넘기고(SDK 기본 = 공식 API), 채우면 그 주소로 보낸다.
종전에는 Anthropic·OpenAI 클라이언트가 키만 받고 Gemini 는 구글 주소가 코드에 박혀 있어,
중계서버가 정해져도 코드를 고치지 않고는 경유시킬 수 없었다(2026-09-10).

anthropic·openai SDK 는 [llm] 추가설치라 로컬 venv 에 없을 수 있다. 없다고 건너뛰면
초록불인데 안 잰 것이 된다 — 가짜 모듈을 끼워 항상 돈다.
"""
from __future__ import annotations

import sys
import types

import pytest

from koipa.config import settings

_GOOGLE = "https://generativelanguage.googleapis.com/v1beta/openai/"
_RELAY = "http://relay.internal:8443/v1"


def _fake_sdk(monkeypatch, modname: str, clsname: str) -> list[dict]:
    calls: list[dict] = []

    class _Client:
        def __init__(self, **kw):
            calls.append(kw)

    mod = types.ModuleType(modname)
    setattr(mod, clsname, _Client)
    monkeypatch.setitem(sys.modules, modname, mod)
    return calls


@pytest.mark.parametrize("relay", ["", _RELAY])
def test_anthropic_passes_relay_only_when_set(monkeypatch, relay):
    calls = _fake_sdk(monkeypatch, "anthropic", "Anthropic")
    monkeypatch.setattr(settings, "anthropic_base_url", relay)
    from koipa.adapters.llm.anthropic_provider import AnthropicProvider

    AnthropicProvider(api_key="k")
    assert calls == [{"api_key": "k"} | ({"base_url": relay} if relay else {})]


@pytest.mark.parametrize("relay", ["", _RELAY])
def test_openai_passes_relay_only_when_set(monkeypatch, relay):
    calls = _fake_sdk(monkeypatch, "openai", "OpenAI")
    monkeypatch.setattr(settings, "openai_base_url", relay)
    from koipa.adapters.llm.openai_provider import OpenAIProvider

    OpenAIProvider(api_key="k")
    assert calls == [{"api_key": "k"} | ({"base_url": relay} if relay else {})]


@pytest.mark.parametrize(("configured", "expected"), [
    (_GOOGLE, _GOOGLE),     # 기본값 — 종전과 같은 구글 주소
    ("", _GOOGLE),          # 비워도 구글 주소로 돌아간다(빈 주소로 호출하지 않는다)
    (_RELAY, _RELAY),       # 중계서버
])
def test_gemini_base_url(monkeypatch, configured, expected):
    calls = _fake_sdk(monkeypatch, "openai", "OpenAI")
    monkeypatch.setattr(settings, "google_base_url", configured)
    monkeypatch.setattr(settings, "google_api_key", "g")
    from koipa.adapters.llm import build_provider

    build_provider("gemini")
    assert calls and calls[-1]["base_url"] == expected
