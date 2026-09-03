"""lifespan 임베더 워밍업 단위 검증.

운영 첫 호출에서 KURE cold start p95 32s가 노출되지 않도록
lifespan에서 1회 호출로 흡수. dryrun에서는 skip.

본 모듈은 lifespan을 직접 부팅하지 않고(다른 production 자격 검증 + OTel +
미들웨어 의존이 너무 많음), _warmup_models 헬퍼를 직접 호출해 격리 검증.

[2026-09] reranker 워밍업 시험 4건은 유사문서 검색(RAG) 폐기로 대상 코드가 없어져 걷었다.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from koipa.api.app import _warmup_models


def _settings(*, poc_mode: str = "full", embedding: str = "hf") -> SimpleNamespace:
    return SimpleNamespace(poc_mode=poc_mode, embedding_provider=embedding)


def test_warmup_skipped_in_dryrun() -> None:
    """dryrun에서는 build_embedder() 호출하지 않음."""
    with patch("koipa.adapters.embedding.build_embedder") as mock_build:
        _warmup_models(_settings(poc_mode="dryrun"))
    assert mock_build.call_count == 0


def test_warmup_runs_embedder_in_full_mode() -> None:
    """full 모드에서는 build_embedder().embed(['warmup']) 1회 호출."""
    mock_emb = MagicMock()
    with patch("koipa.adapters.embedding.build_embedder",
               return_value=mock_emb) as mock_build:
        _warmup_models(_settings(poc_mode="full"))
    assert mock_build.call_count == 1
    mock_emb.embed.assert_called_once_with(["warmup"])


def test_warmup_failure_does_not_block_boot() -> None:
    """build_embedder 실패해도 예외 전파 없음 — silent warn."""
    with patch(
        "koipa.adapters.embedding.build_embedder",
        side_effect=RuntimeError("hf rate limit"),
    ):
        _warmup_models(_settings(poc_mode="full"))  # 예외 안 던지면 PASS
