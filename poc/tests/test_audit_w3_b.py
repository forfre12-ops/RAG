"""Wave3 Track B 회귀 테스트 — RAG/ES/어댑터.

배정 수정 검증:
  [M-es-index]      EsStore.delete_index + swap_alias가 old_index 정리
  [M-rrf]           search_hybrid rrf_constant 기본 60(경로별 RRF 상수 통일)
  [M-rag-fallback]  hybrid→dense 폴백 시 WARNING 로그 + prom counter(hybrid_degrade)
  [M-storage-factory] build_storage가 storage_backend 분기 + LocalStore 오타 수정
  [M-llm-retry]     AnthropicProvider 429/5xx/타임아웃 지수백오프 재시도

본 파일은 Track B 전용(파일명 충돌 없음). 기존 test_es_store.py·test_adapters_storage.py의
계약은 유지되며, 여기서는 새 동작만 추가 커버한다.
"""

from __future__ import annotations

import logging
import warnings
from unittest.mock import MagicMock, patch

import pytest

from lloydk.adapters.vectorstore.base import SearchHit
from lloydk.adapters.vectorstore.es_store import EsStore


# ─────────────────────────────────────────────────────────────
# 공통 헬퍼 — ES 미가용 환경에서 EsStore mock 생성
# ─────────────────────────────────────────────────────────────
def _make_store(version_str: str = "8.15.3", license_type: str = "platinum"):
    mock_client = MagicMock()
    mock_client.info.return_value = {"version": {"number": version_str}}
    mock_client.license.get.return_value = {"license": {"type": license_type}}
    with patch("elasticsearch.Elasticsearch", return_value=mock_client):
        store = EsStore(url="http://mock:9200")
    store._client = mock_client
    return store, mock_client


# ─────────────────────────────────────────────────────────────
# [M-es-index] delete_index + swap_alias가 옛 인덱스 정리
# ─────────────────────────────────────────────────────────────
def test_delete_index_when_exists():
    store, client = _make_store()
    client.indices.exists.return_value = True
    assert store.delete_index("secrets-v1") is True
    client.indices.delete.assert_called_once_with(index="secrets-v1")


def test_delete_index_noop_when_missing():
    store, client = _make_store()
    client.indices.exists.return_value = False
    assert store.delete_index("ghost") is False
    client.indices.delete.assert_not_called()


def test_delete_index_swallows_errors():
    store, client = _make_store()
    client.indices.exists.return_value = True
    client.indices.delete.side_effect = RuntimeError("boom")
    # 정리 실패가 raise되면 재색인 전체가 무효화되므로 best-effort(False 반환).
    assert store.delete_index("secrets-v1") is False


def test_swap_alias_deletes_old_index_after_swap():
    store, client = _make_store()
    client.indices.exists.return_value = True  # delete_index의 exists 체크
    store.swap_alias("secrets-guides-koipa", new_index="v2", old_index="v1")

    # alias swap이 원자적으로 수행됐는지(기존 계약 유지)
    actions = client.indices.update_aliases.call_args.kwargs["actions"]
    assert actions == [
        {"remove": {"index": "v1", "alias": "secrets-guides-koipa"}},
        {"add": {"index": "v2", "alias": "secrets-guides-koipa"}},
    ]
    # 신규 계약: swap 성공 후 옛 인덱스 삭제(blue/green 누적 방지)
    client.indices.delete.assert_called_once_with(index="v1")


def test_swap_alias_skips_delete_when_no_old():
    store, client = _make_store()
    store.swap_alias("alias-x", new_index="v1")
    client.indices.delete.assert_not_called()


def test_swap_alias_skips_delete_when_same_index():
    store, client = _make_store()
    store.swap_alias("alias-x", new_index="v1", old_index="v1")
    # old==new면 삭제하면 안 됨(방금 alias 건 인덱스를 지우는 사고 방지)
    client.indices.delete.assert_not_called()


def test_swap_alias_delete_old_false_disables_cleanup():
    store, client = _make_store()
    client.indices.exists.return_value = True
    store.swap_alias("alias-x", new_index="v2", old_index="v1", delete_old=False)
    client.indices.delete.assert_not_called()


def test_swap_alias_does_not_delete_if_swap_raises():
    store, client = _make_store()
    client.indices.update_aliases.side_effect = RuntimeError("swap failed")
    with pytest.raises(RuntimeError):
        store.swap_alias("alias-x", new_index="v2", old_index="v1")
    # swap이 raise하면 옛 인덱스 삭제까지 도달하면 안 됨
    client.indices.delete.assert_not_called()


# ─────────────────────────────────────────────────────────────
# [M-rrf] RRF 상수 통일 — search_hybrid 기본 rrf_constant=60
# ─────────────────────────────────────────────────────────────
def test_search_hybrid_default_rrf_constant_is_60_retriever():
    """retriever 경로(8.14+)에서 rank_constant 기본 60 전달."""
    store, client = _make_store("8.15.3")
    client.search.return_value = {"hits": {"hits": [{"_id": "a", "_score": 1.0, "_source": {}}]}}
    store.search_hybrid("col", "질의", [0.1] * 8, top_k=5)
    body = client.search.call_args.kwargs["body"]
    assert body["retriever"]["rrf"]["rank_constant"] == 60


def test_search_hybrid_default_rrf_constant_is_60_legacy():
    """legacy 경로(8.12~8.13)에서 rank_constant 기본 60 전달."""
    store, client = _make_store("8.13.0")
    client.search.return_value = {"hits": {"hits": [{"_id": "a", "_score": 1.0, "_source": {}}]}}
    store.search_hybrid("col", "질의", [0.1] * 8, top_k=5)
    body = client.search.call_args.kwargs["body"]
    assert body["rank"]["rrf"]["rank_constant"] == 60


def test_search_hybrid_clientside_uses_60_when_default():
    """clientside 경로(<8.12)는 _rrf_combine k=60으로 결합 — 상수 통일 확인.

    k가 60이면 동점 결과에서 1/(60+rank) 점수가 적용된다. 두 결과 모두에서
    1위인 id가 가장 높은 점수를 받는지로 간접 검증.
    """
    store, client = _make_store("8.10.4")
    client.search.side_effect = [
        {"hits": {"hits": [{"_id": "x", "_score": 2.0, "_source": {}},
                           {"_id": "y", "_score": 1.0, "_source": {}}]}},
        {"hits": {"hits": [{"_id": "x", "_score": 0.9, "_source": {}},
                           {"_id": "z", "_score": 0.8, "_source": {}}]}},
    ]
    hits = store.search_hybrid("col", "질의", [0.1] * 8, top_k=3)
    # x는 양쪽 1위 → 2*(1/61) ≈ 0.03279 로 최상위
    assert hits[0].id == "x"
    assert hits[0].score == pytest.approx(2.0 / 61.0, rel=1e-3)


# ─────────────────────────────────────────────────────────────
# [M-rag-fallback] hybrid→dense 폴백 가시화
# ─────────────────────────────────────────────────────────────
class _HybridFailsStore:
    """search_hybrid는 항상 실패, search(dense)는 성공하는 가짜 스토어."""

    name = "fake"

    def search_hybrid(self, **_kwargs):
        raise RuntimeError("ES down")

    def search(self, *, collection, query, top_k, filter=None):
        return [SearchHit(id="d1", score=1.0, payload={"text": "본문"})]


def test_hybrid_degrade_logs_warning_and_increments_counter(caplog):
    from lloydk.api.prom_metrics import RAG_CONTEXT_FAILURE_TOTAL
    from lloydk.rag import retrieval

    before = RAG_CONTEXT_FAILURE_TOTAL.labels(stage="hybrid_degrade")._value.get()

    with caplog.at_level(logging.WARNING, logger="lloydk.rag.retrieval"):
        hits = retrieval.expand_then_search(
            store=_HybridFailsStore(),
            collection="col",
            query_text="기밀 자료",
            encode=lambda t: [0.1] * 8,
            method="rule",
            top_k=3,
            use_reranker=False,  # reranker wiring 배제 — 폴백 경로만 검증
        )

    # dense 폴백으로 결과는 반환됨
    assert [h.id for h in hits] == ["d1"]
    # WARNING 로그 가시화
    assert any("falling back to dense" in r.message for r in caplog.records)
    # prom counter 증가(stage='hybrid_degrade')
    after = RAG_CONTEXT_FAILURE_TOTAL.labels(stage="hybrid_degrade")._value.get()
    assert after >= before + 1


def test_record_hybrid_degrade_is_best_effort():
    """prom import가 실패해도 _record_hybrid_degrade는 raise하지 않는다."""
    from lloydk.rag import retrieval

    with patch.dict("sys.modules", {"lloydk.api.prom_metrics": None}):
        retrieval._record_hybrid_degrade()  # 예외 없이 통과해야 함


# ─────────────────────────────────────────────────────────────
# [M-storage-factory] build_storage가 storage_backend로 분기 + 오타 수정
# ─────────────────────────────────────────────────────────────
def test_build_storage_force_local_returns_localstorage(tmp_path):
    from lloydk.adapters.storage import LocalStorage, build_storage

    st = build_storage(force_local=True, local_root=str(tmp_path))
    assert isinstance(st, LocalStorage)


def test_build_storage_local_backend(tmp_path):
    from lloydk.adapters.storage import LocalStorage, build_storage

    st = build_storage(backend="local", local_root=str(tmp_path))
    assert isinstance(st, LocalStorage)


def test_build_storage_unknown_backend_falls_back_local(tmp_path):
    from lloydk.adapters.storage import LocalStorage, build_storage

    st = build_storage(backend="bogus-backend", local_root=str(tmp_path))
    assert isinstance(st, LocalStorage)


def test_build_storage_minio_failure_falls_back_local(tmp_path):
    """minio 백엔드 초기화 실패 시 RuntimeWarning + LocalStorage 폴백(비파괴)."""
    from lloydk.adapters import storage as storage_mod
    from lloydk.adapters.storage import LocalStorage, build_storage

    with patch.object(storage_mod, "_build_minio", side_effect=RuntimeError("no minio")):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            st = build_storage(backend="minio", local_root=str(tmp_path))
    assert isinstance(st, LocalStorage)
    assert any(issubclass(w.category, RuntimeWarning) for w in caught)


def test_build_storage_seaweedfs_backend_constructs_seaweed():
    """seaweedfs 백엔드는 SeaweedFSStore를 생성(boto3 lazy라 생성 자체는 성공)."""
    from lloydk.adapters.storage import build_storage
    from lloydk.adapters.storage.seaweedfs_store import SeaweedFSStore

    st = build_storage(backend="seaweedfs")
    assert isinstance(st, SeaweedFSStore)


def test_get_storage_local_typo_fixed():
    """seaweedfs_store.get_storage('local')이 더 이상 ImportError(LocalStore 오타) 안 냄."""
    from lloydk.adapters.storage.local_store import LocalStorage
    from lloydk.adapters.storage.seaweedfs_store import get_storage

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
    from lloydk.adapters.llm import anthropic_provider as ap

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
    from lloydk.adapters.llm.anthropic_provider import _is_retryable

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
