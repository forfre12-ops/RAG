"""Elasticsearch 8.14+ vector store.

doc/13_벡터DB_ES_전환_계획서.md v0.9-final 구현.

- dense_vector + int8_hnsw (8.13+) — 메모리 75% 절감
- 한국어: analysis-nori + userdict_ko.txt
- 하이브리드: retriever API(8.14+) 우선 / legacy rank.rrf 폴백
- 인덱스: `secrets-{role}-{model}-{version}` (테넌트 제거 — 단일 고객사·폐쇄망, 격리는 KL 포털)
- 무중단 재인덱싱: alias 스위칭

PoC 단계에선 실제 ES 클러스터 없이도 import만 안전하도록 elasticsearch 패키지 lazy import.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Sequence

from lloydk.adapters.vectorstore.base import SearchHit

logger = logging.getLogger(__name__)


class EsStore:
    name = "elasticsearch"

    # retriever API GA 버전
    RETRIEVER_API_MIN_VERSION = (8, 14, 0)
    # rank.rrf legacy 지원 최소 버전
    LEGACY_RRF_MIN_VERSION = (8, 12, 0)

    def __init__(
        self,
        url: str | None = None,
        username: str | None = None,
        password: str | None = None,
        api_key: str | None = None,
        verify_certs: bool = True,
        ca_certs: str | None = None,
        request_timeout: float = 30.0,
    ) -> None:
        from elasticsearch import Elasticsearch  # noqa: PLC0415

        from lloydk.config import settings  # noqa: PLC0415

        url = url or settings.es_url
        username = username or settings.es_username or None
        password = password or settings.es_password or None

        kwargs: dict[str, Any] = {
            "hosts": [url],
            "verify_certs": verify_certs,
            "request_timeout": request_timeout,
            # #19: 일시적 타임아웃/네트워크 흔들림에 대한 자동 재시도 — 부분실패 침묵 완화.
            "retry_on_timeout": True,
            "max_retries": 3,
        }
        if ca_certs:
            kwargs["ca_certs"] = ca_certs
        if api_key:
            kwargs["api_key"] = api_key
        elif username and password:
            kwargs["basic_auth"] = (username, password)

        self._client = Elasticsearch(**kwargs)
        self._server_version: tuple[int, int, int] | None = None
        self._license_type: str | None = None
        # #19: 직전 bulk upsert의 실패 건수 — 호출부가 부분실패 여부를 확인 가능.
        self._last_bulk_errors: int = 0

    # ─────────────────────────────────────────────────────────────
    # 서버 버전 감지 (retriever API vs legacy 분기)
    # ─────────────────────────────────────────────────────────────
    def server_version(self) -> tuple[int, int, int]:
        if self._server_version is not None:
            return self._server_version
        info = self._client.info()
        raw = info["version"]["number"]
        m = re.match(r"(\d+)\.(\d+)\.(\d+)", raw)
        if not m:
            raise RuntimeError(f"unparseable ES version: {raw}")
        self._server_version = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
        return self._server_version

    def license_type(self) -> str:
        if self._license_type is not None:
            return self._license_type
        try:
            resp = self._client.license.get()
            self._license_type = resp.get("license", {}).get("type", "basic")
        except Exception:  # noqa: BLE001
            self._license_type = "basic"
        return self._license_type

    def _rrf_licensed(self) -> bool:
        # RRF (retriever API + legacy rank.rrf) 모두 platinum+ 라이선스 필요.
        # basic/gold는 clientside RRF로 폴백.
        return self.license_type() in {"platinum", "enterprise", "trial"}

    def supports_retriever_api(self) -> bool:
        return (
            self.server_version() >= self.RETRIEVER_API_MIN_VERSION
            and self._rrf_licensed()
        )

    def supports_legacy_rrf(self) -> bool:
        return (
            self.server_version() >= self.LEGACY_RRF_MIN_VERSION
            and self._rrf_licensed()
        )

    # ─────────────────────────────────────────────────────────────
    # 인덱스 관리
    # ─────────────────────────────────────────────────────────────
    def ensure_collection(self, name: str, dim: int) -> None:
        """인덱스 생성 (이미 있으면 skip). dim에 맞춰 매핑 자동 생성.

        Note: 운영에서는 인덱스 템플릿(`infra/es/index_template_secrets.json`)이
        `secrets-*` 패턴에 미리 적용돼 있어야 한다. 본 메서드는 PoC·테스트용 폴백.
        """
        if self._client.indices.exists(index=name):
            return

        from lloydk.config import settings  # noqa: PLC0415

        hnsw_type = "int8_hnsw" if self.server_version() >= (8, 13, 0) else "hnsw"

        body = {
            "settings": {
                "index": {
                    "number_of_shards": 1,
                    "number_of_replicas": 0,  # PoC: 1노드 환경에서 yellow 회피
                    "refresh_interval": "5s",
                    "analysis": {
                        "tokenizer": {
                            "korean_nori_tokenizer": {
                                "type": "nori_tokenizer",
                                "decompound_mode": "mixed",
                                "discard_punctuation": "true",
                            }
                        },
                        "analyzer": {
                            "korean_nori": {
                                "type": "custom",
                                "tokenizer": "korean_nori_tokenizer",
                                "filter": [
                                    "lowercase",
                                    "nori_part_of_speech",
                                    "nori_readingform",
                                ],
                            }
                        },
                    },
                }
            },
            "mappings": {
                "properties": {
                    "id": {"type": "keyword"},
                    "doc_id": {"type": "keyword"},
                    "chunk_idx": {"type": "integer"},
                    "grade": {"type": "keyword"},
                    "department": {"type": "keyword"},
                    "doc_type": {"type": "keyword"},
                    "version": {"type": "keyword"},
                    "created_at": {"type": "date"},
                    "text": {
                        "type": "text",
                        "analyzer": "korean_nori",
                        "search_analyzer": "korean_nori",
                    },
                    "embedding": {
                        "type": "dense_vector",
                        "dims": dim,
                        "index": True,
                        "similarity": "cosine",
                        "index_options": {
                            "type": hnsw_type,
                            "m": 16,
                            "ef_construction": 128,
                        },
                    },
                }
            },
        }

        # Nori 플러그인 미설치 환경(테스트) 폴백
        try:
            self._client.indices.create(index=name, **body)
        except Exception as exc:  # noqa: BLE001
            msg = str(exc).lower()
            if "nori" in msg or "analyzer" in msg:
                body["settings"]["index"].pop("analysis", None)
                body["mappings"]["properties"]["text"] = {"type": "text"}
                self._client.indices.create(index=name, **body)
                _ = settings  # 의도적으로 보존 — 향후 fallback 정책 분기에 사용
            else:
                raise

    # ─────────────────────────────────────────────────────────────
    # CRUD
    # ─────────────────────────────────────────────────────────────
    def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[dict] | None = None,
    ) -> int:
        from elasticsearch.helpers import bulk  # noqa: PLC0415

        payloads = payloads or [{} for _ in ids]
        actions = []
        for _id, vec, pl in zip(ids, vectors, payloads, strict=True):
            doc = {**dict(pl), "embedding": list(vec)}
            actions.append(
                {
                    "_op_type": "index",
                    "_index": collection,
                    "_id": _id,
                    "_source": doc,
                }
            )
        # #19: raise_on_error=False라 부분실패가 조용히 묻힌다. errors(실패 항목)를
        # 받아 실패 건수·사유를 logger.warning으로 남겨 무관측을 해소한다.
        success, errors = bulk(self._client, actions, refresh="wait_for", raise_on_error=False)
        # errors는 raise_on_error=False일 때 실패 액션의 리스트(또는 int). 방어적으로 처리.
        failed = errors if isinstance(errors, int) else len(errors)
        if failed:
            sample = errors[:5] if isinstance(errors, list) else None
            logger.warning(
                "ES bulk 색인 부분실패: index=%s total=%d success=%d failed=%d sample=%r",
                collection,
                len(actions),
                int(success),
                failed,
                sample,
            )
        # 정상 경로 동작 보존: 기존처럼 success_count를 반환.
        # (호출부가 부분실패를 알 수 있도록 self._last_bulk_errors에도 실패 수를 노출)
        self._last_bulk_errors = failed
        return int(success)

    def delete(
        self,
        collection: str,
        *,
        ids: Sequence[str] | None = None,
        filter: dict | None = None,
    ) -> int:
        """#17: 재인덱싱 고아 청크 제거. ids 또는 filter(예: {"doc_id": ...})로 삭제.

        - filter: metadata 필드 term 일치로 delete_by_query (예: doc_id 기준).
        - ids: 정확 _id 목록을 delete_by_query(terms _id)로 삭제.
        - 인덱스가 없으면 no-op(0 반환) — 멱등 재인덱싱 보장.
        - 삭제된 문서 수를 반환.
        """
        if not ids and not filter:
            return 0
        if not self._client.indices.exists(index=collection):
            return 0

        bool_filter: list[dict] = _build_term_filters(filter)
        if ids:
            bool_filter.append({"terms": {"_id": list(ids)}})

        query = {"bool": {"filter": bool_filter}} if bool_filter else {"match_all": {}}
        resp = self._client.delete_by_query(
            index=collection,
            query=query,
            refresh=True,
            conflicts="proceed",
        )
        return int(resp.get("deleted", 0))

    def search(
        self,
        collection: str,
        query: Sequence[float],
        top_k: int = 5,
        filter: dict | None = None,
    ) -> list[SearchHit]:
        es_filter = _build_term_filters(filter)
        num_candidates = _num_candidates_for(top_k, has_filter=bool(es_filter))

        knn = {
            "field": "embedding",
            "query_vector": list(query),
            "k": top_k,
            "num_candidates": num_candidates,
        }
        if es_filter:
            knn["filter"] = es_filter

        resp = self._client.search(index=collection, knn=knn, size=top_k)
        return _hits_from_response(resp)

    def search_hybrid(
        self,
        collection: str,
        query_text: str,
        query_vec: Sequence[float],
        top_k: int = 5,
        filter: dict | None = None,
        rrf_window: int = 50,
        rrf_constant: int = 60,
        use_retriever_api: bool | None = None,
    ) -> list[SearchHit]:
        """BM25 + dense kNN + RRF 하이브리드.

        - 8.14+: retriever API
        - 8.12~8.13: legacy `rank.rrf`
        - <8.12: 클라이언트단 RRF

        rrf_constant 기본값은 60(Cormack et al. 2009 표준). 모든 경로(retriever
        rank_constant / legacy rank_constant / clientside _rrf_combine k)가 동일
        상수를 쓰도록 통일해 경로별 융합 결과 편차를 제거한다.
        """
        es_filter = _build_term_filters(filter)
        num_candidates = _num_candidates_for(top_k, has_filter=bool(es_filter))

        if use_retriever_api is None:
            use_retriever_api = self.supports_retriever_api()

        if use_retriever_api:
            return self._search_hybrid_retriever(
                collection, query_text, query_vec, top_k,
                es_filter, num_candidates, rrf_window, rrf_constant,
            )
        if self.supports_legacy_rrf():
            return self._search_hybrid_legacy(
                collection, query_text, query_vec, top_k,
                es_filter, num_candidates, rrf_window, rrf_constant,
            )
        return self._search_hybrid_clientside(
            collection, query_text, query_vec, top_k,
            es_filter, num_candidates, rrf_constant,
        )

    def _search_hybrid_retriever(
        self,
        collection: str,
        query_text: str,
        query_vec: Sequence[float],
        top_k: int,
        es_filter: list[dict],
        num_candidates: int,
        rrf_window: int,
        rrf_constant: int,
    ) -> list[SearchHit]:
        bm25_query: dict[str, Any] = {"bool": {"must": [{"match": {"text": query_text}}]}}
        if es_filter:
            bm25_query["bool"]["filter"] = es_filter

        knn: dict[str, Any] = {
            "field": "embedding",
            "query_vector": list(query_vec),
            "k": top_k,
            "num_candidates": num_candidates,
        }
        if es_filter:
            knn["filter"] = es_filter

        body = {
            "size": top_k,
            "retriever": {
                "rrf": {
                    "rank_window_size": rrf_window,
                    "rank_constant": rrf_constant,
                    "retrievers": [
                        {"standard": {"query": bm25_query}},
                        {"knn": knn},
                    ],
                }
            },
        }
        resp = self._client.search(index=collection, body=body)
        return _hits_from_response(resp)

    def _search_hybrid_legacy(
        self,
        collection: str,
        query_text: str,
        query_vec: Sequence[float],
        top_k: int,
        es_filter: list[dict],
        num_candidates: int,
        rrf_window: int,
        rrf_constant: int,
    ) -> list[SearchHit]:
        body: dict[str, Any] = {
            "size": top_k,
            "query": {"bool": {"must": [{"match": {"text": query_text}}]}},
            "knn": {
                "field": "embedding",
                "query_vector": list(query_vec),
                "k": top_k,
                "num_candidates": num_candidates,
            },
            "rank": {"rrf": {"rank_window_size": rrf_window, "rank_constant": rrf_constant}},
        }
        if es_filter:
            body["query"]["bool"]["filter"] = es_filter
            body["knn"]["filter"] = es_filter
        resp = self._client.search(index=collection, body=body)
        return _hits_from_response(resp)

    def _search_hybrid_clientside(
        self,
        collection: str,
        query_text: str,
        query_vec: Sequence[float],
        top_k: int,
        es_filter: list[dict],
        num_candidates: int,
        rrf_constant: int,
    ) -> list[SearchHit]:
        # BM25
        bm25_body: dict[str, Any] = {
            "size": num_candidates,
            "query": {"bool": {"must": [{"match": {"text": query_text}}]}},
        }
        if es_filter:
            bm25_body["query"]["bool"]["filter"] = es_filter
        bm25_resp = self._client.search(index=collection, body=bm25_body)
        bm25_hits = _hits_from_response(bm25_resp)

        # kNN
        knn_body: dict[str, Any] = {
            "size": num_candidates,
            "knn": {
                "field": "embedding",
                "query_vector": list(query_vec),
                "k": num_candidates,
                "num_candidates": num_candidates,
            },
        }
        if es_filter:
            knn_body["knn"]["filter"] = es_filter
        knn_resp = self._client.search(index=collection, body=knn_body)
        knn_hits = _hits_from_response(knn_resp)

        return _rrf_combine(bm25_hits, knn_hits, top_k=top_k, k=rrf_constant)

    def count(self, collection: str) -> int:
        if not self._client.indices.exists(index=collection):
            return 0
        return int(self._client.count(index=collection)["count"])

    # ─────────────────────────────────────────────────────────────
    # alias 관리 (무중단 재인덱싱용)
    # ─────────────────────────────────────────────────────────────
    def delete_index(self, name: str) -> bool:
        """인덱스 삭제. 존재하지 않으면 no-op. 삭제 시도 결과를 bool로 반환.

        blue/green 재색인 누적 방지용 — alias swap 성공 후 이전 인덱스 정리.
        실패해도 raise하지 않고 False를 반환(정리 실패가 재색인 자체를 무효화하면 안 됨).
        """
        try:
            if not self._client.indices.exists(index=name):
                return False
            self._client.indices.delete(index=name)
            return True
        except Exception:  # noqa: BLE001
            return False

    def swap_alias(
        self,
        alias: str,
        new_index: str,
        old_index: str | None = None,
        *,
        delete_old: bool = True,
    ) -> None:
        """alias를 new_index로 원자적 스위칭.

        delete_old=True(기본)면 swap이 성공하고 old_index가 new_index와 다를 때
        old_index를 삭제해 blue/green 재색인 누적을 막는다. 삭제는 best-effort(실패 무시).
        """
        actions: list[dict] = []
        if old_index and old_index != new_index:
            actions.append({"remove": {"index": old_index, "alias": alias}})
        actions.append({"add": {"index": new_index, "alias": alias}})
        self._client.indices.update_aliases(actions=actions)

        # swap이 성공한 뒤에만 옛 인덱스 정리 — 위 호출이 raise하면 여기 도달 안 함.
        if delete_old and old_index and old_index != new_index:
            self.delete_index(old_index)


# ─────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────
def _build_term_filters(filter: dict | None) -> list[dict]:
    if not filter:
        return []
    return [{"term": {k: v}} for k, v in filter.items()]


def _num_candidates_for(top_k: int, *, has_filter: bool) -> int:
    """필터 셀렉티비티에 따른 num_candidates 가이드 (doc/13 §4.3·§7).

    - 무필터·약필터: max(100, top_k*20)
    - 강필터: top_k*50 (selectivity 정보가 없으므로 보수적으로)
    """
    base = max(100, top_k * 20)
    return base if not has_filter else max(base, top_k * 50)


def _hits_from_response(resp: dict) -> list[SearchHit]:
    out: list[SearchHit] = []
    for h in resp["hits"]["hits"]:
        src = dict(h.get("_source") or {})
        src.pop("embedding", None)  # 벡터는 payload에서 제외
        out.append(SearchHit(id=str(h["_id"]), score=float(h.get("_score") or 0.0), payload=src))
    return out


def _rrf_combine(
    a: list[SearchHit],
    b: list[SearchHit],
    *,
    top_k: int,
    k: int = 60,
) -> list[SearchHit]:
    """Reciprocal Rank Fusion: score = sum(1 / (k + rank_i))."""
    scores: dict[str, float] = {}
    payloads: dict[str, dict] = {}
    for rank, hit in enumerate(a, start=1):
        scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank)
        payloads.setdefault(hit.id, hit.payload)
    for rank, hit in enumerate(b, start=1):
        scores[hit.id] = scores.get(hit.id, 0.0) + 1.0 / (k + rank)
        payloads.setdefault(hit.id, hit.payload)
    ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_k]
    return [SearchHit(id=_id, score=s, payload=payloads.get(_id, {})) for _id, s in ranked]
