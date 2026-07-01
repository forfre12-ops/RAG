"""EsStore 단위 테스트 (mocked Elasticsearch).

doc/13_벡터DB_ES_전환_계획서.md §6 S2 산출물.

검증 범위:
- pure helper: _build_term_filters, _num_candidates_for, _hits_from_response, _rrf_combine
- 버전 감지 및 retriever/legacy/clientside 분기
- search/search_hybrid 호출 시 ES 클라이언트가 받는 body 형태
- upsert가 _bulk를 통해 호출되는지
- ensure_collection 인덱스 생성 매핑 (Nori 폴백 포함)
- alias 스위칭
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from lloydk.adapters.vectorstore.base import SearchHit
from lloydk.adapters.vectorstore.es_store import (
    EsStore,
    _build_term_filters,
    _hits_from_response,
    _num_candidates_for,
    _rrf_combine,
)


# ─────────────────────────────────────────────────────────────
# Pure helpers
# ─────────────────────────────────────────────────────────────


def test_build_term_filters_empty():
    assert _build_term_filters(None) == []
    assert _build_term_filters({}) == []


def test_build_term_filters_basic():
    out = _build_term_filters({"doc_type": "guideline", "grade": "TS"})
    assert {"term": {"doc_type": "guideline"}} in out
    assert {"term": {"grade": "TS"}} in out
    assert len(out) == 2


def test_num_candidates_no_filter():
    # 무필터: max(100, top_k*20)
    assert _num_candidates_for(5, has_filter=False) == 100
    assert _num_candidates_for(20, has_filter=False) == 400


def test_num_candidates_strong_filter():
    # 필터 있음: max(base, top_k*50)
    assert _num_candidates_for(5, has_filter=True) == 250
    assert _num_candidates_for(20, has_filter=True) == 1000


def test_hits_from_response_strips_embedding():
    resp = {
        "hits": {
            "hits": [
                {
                    "_id": "doc-1",
                    "_score": 1.23,
                    "_source": {
                        "doc_id": "abc",
                        "grade": "TS",
                        "embedding": [0.1] * 1024,  # 응답에서 제거되어야 함
                    },
                },
                {"_id": "doc-2", "_score": 0.5, "_source": {"grade": "S1"}},
            ]
        }
    }
    hits = _hits_from_response(resp)
    assert len(hits) == 2
    assert hits[0].id == "doc-1"
    assert hits[0].score == pytest.approx(1.23)
    assert "embedding" not in hits[0].payload
    assert hits[0].payload["grade"] == "TS"


def test_rrf_combine_intersection_ranks_first():
    """양쪽 검색 결과에 모두 등장한 doc이 상위에 와야 한다."""
    a = [SearchHit(id="x", score=10.0), SearchHit(id="y", score=8.0), SearchHit(id="z", score=5.0)]
    b = [SearchHit(id="y", score=0.9), SearchHit(id="w", score=0.8), SearchHit(id="x", score=0.5)]
    out = _rrf_combine(a, b, top_k=3, k=10)
    top_ids = {h.id for h in out}
    # y는 양쪽 1·2위, x는 양쪽 1·3위 → 상위 점령
    assert "y" in top_ids
    assert "x" in top_ids


def test_rrf_combine_disjoint_preserves_order():
    """교집합 없으면 각자 rank 합으로 정렬."""
    a = [SearchHit(id="a1", score=10.0), SearchHit(id="a2", score=8.0)]
    b = [SearchHit(id="b1", score=0.9), SearchHit(id="b2", score=0.8)]
    out = _rrf_combine(a, b, top_k=4, k=60)
    # 같은 rank 1끼리는 동점 → 추가 동점일 때 dict insertion order
    assert out[0].id in {"a1", "b1"}
    assert len(out) == 4


# ─────────────────────────────────────────────────────────────
# 서버 버전 감지 및 하이브리드 분기
# ─────────────────────────────────────────────────────────────


def _make_store_with_version(
    version_str: str, license_type: str = "platinum"
) -> tuple[EsStore, MagicMock]:
    """ES 서버 미가용 환경에서 EsStore를 mock하여 생성.

    license_type 기본값을 platinum으로 두어 RRF 분기 테스트가 라이선스에 안 막히게 한다.
    basic 환경 분기 검증은 별도 케이스에서 명시적으로 지정.
    """
    mock_client = MagicMock()
    mock_client.info.return_value = {"version": {"number": version_str}}
    mock_client.license.get.return_value = {"license": {"type": license_type}}

    with patch("elasticsearch.Elasticsearch", return_value=mock_client):
        store = EsStore(url="http://mock:9200")
    # 직접 client 주입 (생성자에서 만들어진 것과 같은 mock이지만 명시적으로 보장)
    store._client = mock_client
    return store, mock_client


def test_server_version_parses_retriever_capable():
    store, _ = _make_store_with_version("8.15.3")
    assert store.server_version() == (8, 15, 3)
    assert store.supports_retriever_api() is True
    assert store.supports_legacy_rrf() is True


def test_server_version_legacy_only():
    store, _ = _make_store_with_version("8.13.0")
    assert store.supports_retriever_api() is False
    assert store.supports_legacy_rrf() is True


def test_server_version_pre_rrf():
    store, _ = _make_store_with_version("8.10.4")
    assert store.supports_retriever_api() is False
    assert store.supports_legacy_rrf() is False


def test_basic_license_falls_back_to_clientside_rrf():
    # ES 8.15 + basic 라이선스: RRF 모두 미허용 → clientside RRF 폴백
    store, _ = _make_store_with_version("8.15.3", license_type="basic")
    assert store.license_type() == "basic"
    assert store.supports_retriever_api() is False
    assert store.supports_legacy_rrf() is False


def test_platinum_license_allows_retriever_api():
    store, _ = _make_store_with_version("8.15.3", license_type="platinum")
    assert store.supports_retriever_api() is True


def test_trial_license_allows_rrf():
    store, _ = _make_store_with_version("8.13.0", license_type="trial")
    assert store.supports_legacy_rrf() is True


def test_license_lookup_failure_treated_as_basic():
    store, client = _make_store_with_version("8.15.3")
    client.license.get.side_effect = RuntimeError("license api unavailable")
    store._license_type = None
    assert store.license_type() == "basic"
    assert store.supports_retriever_api() is False


def test_server_version_unparseable():
    store, client = _make_store_with_version("not-a-version")
    # cache 무효화 후 재호출
    store._server_version = None
    client.info.return_value = {"version": {"number": "weird"}}
    with pytest.raises(RuntimeError, match="unparseable"):
        store.server_version()


# ─────────────────────────────────────────────────────────────
# search / search_hybrid 분기
# ─────────────────────────────────────────────────────────────


def _search_response(ids: list[str]) -> dict:
    return {
        "hits": {
            "hits": [
                {"_id": _id, "_score": 1.0 - i * 0.1, "_source": {"grade": "TS"}}
                for i, _id in enumerate(ids)
            ]
        }
    }


def test_search_uses_knn_with_num_candidates():
    store, client = _make_store_with_version("8.15.3")
    client.search.return_value = _search_response(["d1", "d2"])

    hits = store.search("col", [0.1] * 8, top_k=5, filter={"doc_type": "guideline"})

    assert [h.id for h in hits] == ["d1", "d2"]
    # ES가 한 번 호출, knn 객체에 filter 포함
    client.search.assert_called_once()
    kwargs = client.search.call_args.kwargs
    assert kwargs["index"] == "col"
    assert kwargs["size"] == 5
    knn = kwargs["knn"]
    assert knn["field"] == "embedding"
    assert knn["k"] == 5
    # 필터 selectivity 강 → num_candidates 상향
    assert knn["num_candidates"] >= 250
    assert {"term": {"doc_type": "guideline"}} in knn["filter"]


def test_search_hybrid_dispatches_retriever_on_8_15():
    store, client = _make_store_with_version("8.15.3")
    client.search.return_value = _search_response(["a", "b", "c"])

    store.search_hybrid("col", "기술자료임치", [0.1] * 8, top_k=5)

    body = client.search.call_args.kwargs["body"]
    # retriever API 사용 확인
    assert "retriever" in body
    assert "rrf" in body["retriever"]
    retrievers = body["retriever"]["rrf"]["retrievers"]
    assert any("standard" in r for r in retrievers)
    assert any("knn" in r for r in retrievers)
    # legacy 키는 들어가지 않아야 함
    assert "rank" not in body


def test_search_hybrid_dispatches_legacy_on_8_13():
    store, client = _make_store_with_version("8.13.0")
    client.search.return_value = _search_response(["a", "b"])

    store.search_hybrid("col", "회사 매출 자료", [0.1] * 8, top_k=3)

    body = client.search.call_args.kwargs["body"]
    # legacy rank.rrf 사용 확인
    assert "rank" in body
    assert "rrf" in body["rank"]
    assert "query" in body
    assert "knn" in body
    # retriever 키는 없어야 함
    assert "retriever" not in body


def test_search_hybrid_dispatches_clientside_on_pre_rrf():
    store, client = _make_store_with_version("8.10.4")
    # 두 번 호출됨 (BM25 한 번, kNN 한 번)
    client.search.side_effect = [
        _search_response(["x", "y", "z"]),
        _search_response(["y", "w", "x"]),
    ]

    hits = store.search_hybrid("col", "기밀", [0.1] * 8, top_k=3)

    assert client.search.call_count == 2
    # 첫 호출은 BM25(쿼리 텍스트), 두 번째는 kNN
    first_body = client.search.call_args_list[0].kwargs["body"]
    second_body = client.search.call_args_list[1].kwargs["body"]
    assert "match" in first_body["query"]["bool"]["must"][0]
    assert "knn" in second_body
    # 양쪽에 등장한 id가 상위에 와야 함
    top_ids = [h.id for h in hits]
    assert "x" in top_ids or "y" in top_ids


def test_search_hybrid_explicit_use_retriever_api_overrides_version():
    """명시적 use_retriever_api=False 시 버전 무관 legacy 경로."""
    store, client = _make_store_with_version("8.15.3")
    client.search.return_value = _search_response(["a"])

    store.search_hybrid(
        "col", "텍스트", [0.1] * 8, top_k=2, use_retriever_api=False,
    )

    body = client.search.call_args.kwargs["body"]
    assert "rank" in body  # legacy 강제


def test_search_hybrid_filter_propagation_retriever():
    store, client = _make_store_with_version("8.15.3")
    client.search.return_value = _search_response(["a"])

    store.search_hybrid(
        "col",
        "텍스트",
        [0.1] * 8,
        top_k=5,
        filter={"doc_type": "guideline", "grade": "TS"},
    )

    body = client.search.call_args.kwargs["body"]
    retrievers = body["retriever"]["rrf"]["retrievers"]
    standard = next(r["standard"] for r in retrievers if "standard" in r)
    knn = next(r["knn"] for r in retrievers if "knn" in r)
    # 양쪽에 filter 동시 적용
    assert {"term": {"doc_type": "guideline"}} in standard["query"]["bool"]["filter"]
    assert {"term": {"doc_type": "guideline"}} in knn["filter"]


# ─────────────────────────────────────────────────────────────
# upsert / count / alias
# ─────────────────────────────────────────────────────────────


def test_upsert_calls_bulk_with_correct_actions():
    store, _client = _make_store_with_version("8.15.3")

    with patch("elasticsearch.helpers.bulk", return_value=(2, [])) as mock_bulk:
        n = store.upsert(
            "col",
            ids=["a", "b"],
            vectors=[[1.0, 0.0], [0.0, 1.0]],
            payloads=[{"grade": "TS"}, {"grade": "S1"}],
        )

    assert n == 2
    mock_bulk.assert_called_once()
    actions = list(mock_bulk.call_args.args[1])
    assert len(actions) == 2
    assert actions[0]["_index"] == "col"
    assert actions[0]["_id"] == "a"
    assert actions[0]["_op_type"] == "index"
    src = actions[0]["_source"]
    assert src["grade"] == "TS"
    assert src["embedding"] == [1.0, 0.0]


def test_count_returns_zero_when_index_missing():
    store, client = _make_store_with_version("8.15.3")
    client.indices.exists.return_value = False
    assert store.count("missing") == 0
    client.count.assert_not_called()


def test_count_returns_es_count_when_index_exists():
    store, client = _make_store_with_version("8.15.3")
    client.indices.exists.return_value = True
    client.count.return_value = {"count": 42}
    assert store.count("col") == 42


def test_ensure_collection_skips_when_exists():
    store, client = _make_store_with_version("8.15.3")
    client.indices.exists.return_value = True
    store.ensure_collection("col", dim=1024)
    client.indices.create.assert_not_called()


def test_ensure_collection_uses_int8_hnsw_on_8_13_plus():
    store, client = _make_store_with_version("8.15.3")
    client.indices.exists.return_value = False
    store.ensure_collection("col", dim=1024)

    create_kwargs = client.indices.create.call_args.kwargs
    body = create_kwargs.get("mappings") or create_kwargs["mappings"]
    emb_mapping = body["properties"]["embedding"]
    assert emb_mapping["dims"] == 1024
    assert emb_mapping["index_options"]["type"] == "int8_hnsw"


def test_ensure_collection_falls_back_to_hnsw_on_pre_8_13():
    store, client = _make_store_with_version("8.12.2")
    client.indices.exists.return_value = False
    store.ensure_collection("col", dim=768)

    create_kwargs = client.indices.create.call_args.kwargs
    body = create_kwargs["mappings"]
    assert body["properties"]["embedding"]["index_options"]["type"] == "hnsw"


def test_ensure_collection_nori_fallback_on_plugin_missing():
    """analysis-nori 미설치 시 plain text 매핑으로 폴백한다."""
    store, client = _make_store_with_version("8.15.3")
    client.indices.exists.return_value = False

    # 첫 번째 create는 nori 관련 에러, 두 번째는 성공
    client.indices.create.side_effect = [
        Exception("analyzer [korean_nori] uses nori which is not installed"),
        {"acknowledged": True},
    ]

    store.ensure_collection("col", dim=1024)

    assert client.indices.create.call_count == 2
    second_body = client.indices.create.call_args_list[1].kwargs["mappings"]
    assert second_body["properties"]["text"] == {"type": "text"}
    # analysis 설정도 제거됨
    second_settings = client.indices.create.call_args_list[1].kwargs["settings"]
    assert "analysis" not in second_settings["index"]


def test_swap_alias_atomic_remove_add():
    store, client = _make_store_with_version("8.15.3")
    store.swap_alias("secrets-guides-koipa", new_index="v2", old_index="v1")

    actions = client.indices.update_aliases.call_args.kwargs["actions"]
    assert actions == [
        {"remove": {"index": "v1", "alias": "secrets-guides-koipa"}},
        {"add": {"index": "v2", "alias": "secrets-guides-koipa"}},
    ]


def test_swap_alias_add_only_when_no_old():
    store, client = _make_store_with_version("8.15.3")
    store.swap_alias("alias-x", new_index="v1")

    actions = client.indices.update_aliases.call_args.kwargs["actions"]
    assert actions == [{"add": {"index": "v1", "alias": "alias-x"}}]
