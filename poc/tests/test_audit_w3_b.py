"""Wave3 Track B 회귀 테스트 — 스토리지 어댑터 · LLM 재시도.

배정 수정 검증:
  [M-storage-factory] build_storage가 storage_backend 분기 + LocalStore 오타 수정
  [M-llm-retry]     AnthropicProvider 429/5xx/타임아웃 지수백오프 재시도

[2026-09] 종전에 함께 있던 [M-es-index]·[M-rrf]·[M-rag-fallback] 세 절은
유사문서 검색(RAG) 폐기로 대상 코드가 없어져 걷었다.
"""

from __future__ import annotations

import warnings
from unittest.mock import MagicMock, patch

import pytest


# ─────────────────────────────────────────────────────────────
# [M-storage-factory] build_storage가 storage_backend로 분기 + 오타 수정
# ─────────────────────────────────────────────────────────────
def test_build_storage_force_local_returns_localstorage(tmp_path):
    from koipa.adapters.storage import LocalStorage, build_storage

    st = build_storage(force_local=True, local_root=str(tmp_path))
    assert isinstance(st, LocalStorage)


def test_build_storage_local_backend(tmp_path):
    from koipa.adapters.storage import LocalStorage, build_storage

    st = build_storage(backend="local", local_root=str(tmp_path))
    assert isinstance(st, LocalStorage)


def test_build_storage_unknown_backend_falls_back_local(tmp_path):
    from koipa.adapters.storage import LocalStorage, build_storage

    st = build_storage(backend="bogus-backend", local_root=str(tmp_path))
    assert isinstance(st, LocalStorage)


def test_build_storage_minio_failure_falls_back_local(tmp_path):
    """minio 백엔드 초기화 실패 시 RuntimeWarning + LocalStorage 폴백(비파괴)."""
    from koipa.adapters import storage as storage_mod
    from koipa.adapters.storage import LocalStorage, build_storage

    with patch.object(storage_mod, "_build_minio", side_effect=RuntimeError("no minio")):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            st = build_storage(backend="minio", local_root=str(tmp_path))
    assert isinstance(st, LocalStorage)
    assert any(issubclass(w.category, RuntimeWarning) for w in caught)


def test_build_storage_minio_failure_raises_in_full_mode(monkeypatch, tmp_path):
    from koipa.adapters import storage as storage_mod
    from koipa.adapters.storage import build_storage

    monkeypatch.setattr(storage_mod, "_strict_remote_storage_required", lambda: True)
    with patch.object(storage_mod, "_build_minio", side_effect=RuntimeError("no minio")):
        with pytest.raises(RuntimeError, match="full mode"):
            build_storage(backend="minio", local_root=str(tmp_path))


def test_build_storage_seaweedfs_backend_constructs_seaweed():
    """seaweedfs 백엔드는 SeaweedFSStore를 생성(boto3 lazy라 생성 자체는 성공)."""
    from koipa.adapters.storage import build_storage
    from koipa.adapters.storage.seaweedfs_store import SeaweedFSStore

    st = build_storage(backend="seaweedfs")
    assert isinstance(st, SeaweedFSStore)


def test_get_storage_local_typo_fixed():
    """seaweedfs_store.get_storage('local')이 더 이상 ImportError(LocalStore 오타) 안 냄."""
    from koipa.adapters.storage.local_store import LocalStorage
    from koipa.adapters.storage.seaweedfs_store import get_storage

    st = get_storage("local")
    assert isinstance(st, LocalStorage)


# ─────────────────────────────────────────────────────────────
# [M-llm-retry] 지수 백오프 재시도
# ─────────────────────────────────────────────────────────────
class _Status429(Exception):
    status_code = 429


class _Status500(Exception):
    status_code = 500


class _Status400(Exception):
    status_code = 400


class APITimeoutError(Exception):
    """anthropic.APITimeoutError 흉내 — status_code 없음, 클래스명으로 분류."""


def _make_provider(monkeypatch):
    """anthropic 미설치 환경에서 AnthropicProvider를 생성(client만 mock)."""
    from koipa.adapters.llm import anthropic_provider as ap

    prov = ap.AnthropicProvider.__new__(ap.AnthropicProvider)
    prov._client = MagicMock()
    prov.model = "claude-sonnet-4-6"
    prov._max_retries = 3
    prov._base_delay = 0.0  # 테스트 속도 — sleep 0
    prov._max_delay = 0.0
    return prov


def _ok_message():
    msg = MagicMock()
    block = MagicMock()
    block.type = "text"
    block.text = "응답"
    msg.content = [block]
    msg.usage.input_tokens = 10
    msg.usage.output_tokens = 5
    return msg


def test_retry_classifier():
    from koipa.adapters.llm.anthropic_provider import _is_retryable

    assert _is_retryable(_Status429()) is True
    assert _is_retryable(_Status500()) is True
    assert _is_retryable(_Status400()) is False
    assert _is_retryable(APITimeoutError()) is True
    assert _is_retryable(ValueError("nope")) is False


def test_generate_retries_on_429_then_succeeds(monkeypatch):
    prov = _make_provider(monkeypatch)
    prov._client.messages.create.side_effect = [_Status429(), _ok_message()]

    resp = prov.generate("프롬프트")
    assert resp.usage.success is True
    assert resp.text == "응답"
    assert prov._client.messages.create.call_count == 2


def test_generate_retries_exhausted_returns_failure(monkeypatch):
    prov = _make_provider(monkeypatch)
    prov._client.messages.create.side_effect = _Status500()

    resp = prov.generate("프롬프트")
    assert resp.usage.success is False
    assert resp.usage.error_code == "_Status500"
    # 1(본 호출) + 3(재시도) = 4회 시도
    assert prov._client.messages.create.call_count == 4
    assert resp.meta["retries_exhausted"] is True


def test_generate_no_retry_on_4xx(monkeypatch):
    prov = _make_provider(monkeypatch)
    prov._client.messages.create.side_effect = _Status400()

    resp = prov.generate("프롬프트")
    assert resp.usage.success is False
    # 400은 항구적 — 재시도 없이 1회만
    assert prov._client.messages.create.call_count == 1
    assert resp.meta["retries_exhausted"] is False


def test_generate_retries_on_timeout(monkeypatch):
    prov = _make_provider(monkeypatch)
    prov._client.messages.create.side_effect = [APITimeoutError(), APITimeoutError(), _ok_message()]

    resp = prov.generate("프롬프트")
    assert resp.usage.success is True
    assert prov._client.messages.create.call_count == 3
