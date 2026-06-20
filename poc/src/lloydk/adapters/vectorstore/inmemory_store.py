"""In-memory cosine similarity store. PoC·테스트용.

v2.2 (2026-05-28): 간단한 BM25 (token-overlap 기반) + RRF 하이브리드 지원.
- payload["text"]가 있으면 BM25 점수 계산, vector cosine과 RRF로 결합.
- query_text가 None/빈 문자열이면 종전과 같이 vec-only 폴백(RuntimeWarning).
- 진짜 BM25 (lucene 토크나이저·언어별 analyzer)는 EsStore. 본 구현은
  외부 의존 없는 PoC·dryrun 폴백용.
"""

from __future__ import annotations

import math
import re
import warnings
from dataclasses import dataclass, field
from typing import Sequence

from lloydk.adapters.vectorstore.base import SearchHit


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    """간단한 토크나이저 — `\\w+` 매칭 + 소문자. 한글도 \\w에 포함된다."""
    if not text:
        return []
    return _TOKEN_RE.findall(text.lower())


@dataclass
class _Collection:
    dim: int
    ids: list[str] = field(default_factory=list)
    vectors: list[list[float]] = field(default_factory=list)
    payloads: list[dict] = field(default_factory=list)


class InMemoryStore:
    name = "inmemory"

    def __init__(self) -> None:
        self._cols: dict[str, _Collection] = {}

    def ensure_collection(self, name: str, dim: int) -> None:
        col = self._cols.get(name)
        if col is None:
            self._cols[name] = _Collection(dim=dim)
        elif col.dim != dim:
            raise ValueError(f"dim mismatch on {name}: {col.dim} vs {dim}")

    def upsert(
        self,
        collection: str,
        ids: Sequence[str],
        vectors: Sequence[Sequence[float]],
        payloads: Sequence[dict] | None = None,
    ) -> int:
        col = self._cols[collection]
        payloads = payloads or [{} for _ in ids]
        for _id, vec, pl in zip(ids, vectors, payloads, strict=True):
            if _id in col.ids:
                i = col.ids.index(_id)
                col.vectors[i] = list(vec)
                col.payloads[i] = dict(pl)
            else:
                col.ids.append(_id)
                col.vectors.append(list(vec))
                col.payloads.append(dict(pl))
        return len(ids)

    def delete(
        self,
        collection: str,
        *,
        ids: Sequence[str] | None = None,
        filter: dict | None = None,
    ) -> int:
        """#17: 재인덱싱 고아 청크 제거. ids 또는 filter(예: {"doc_id": ...})로 삭제.

        - filter: payload 필드 일치 항목 제거(예: doc_id 기준).
        - ids: 정확 id 일치 항목 제거.
        - 컬렉션이 없거나 대상이 없으면 0 반환(멱등).
        - 제거된 항목 수를 반환.
        """
        col = self._cols.get(collection)
        if col is None or (not ids and not filter):
            return 0

        id_set = set(ids) if ids else None
        keep_i: list[int] = []
        removed = 0
        for i, (_id, pl) in enumerate(zip(col.ids, col.payloads, strict=True)):
            match_id = id_set is not None and _id in id_set
            match_filter = filter is not None and all(pl.get(k) == v for k, v in filter.items())
            if match_id or match_filter:
                removed += 1
            else:
                keep_i.append(i)

        if removed:
            col.ids = [col.ids[i] for i in keep_i]
            col.vectors = [col.vectors[i] for i in keep_i]
            col.payloads = [col.payloads[i] for i in keep_i]
        return removed

    def sample_vectors(self, *, limit: int = 200, collection: str | None = None) -> list[list[float]]:
        """A4: drift_monitor용 — 최근 저장 벡터 표본.

        시간 인덱스 없어 마지막 N개를 그대로 반환. ES 백엔드에서 timestamp range로
        교체할 것. collection 미지정 시 모든 컬렉션을 합쳐서 표본.
        """
        out: list[list[float]] = []
        cols = [self._cols[collection]] if collection and collection in self._cols else self._cols.values()
        for col in cols:
            for vec in col.vectors[-limit:]:
                out.append(list(vec))
                if len(out) >= limit:
                    return out
        return out

    def search(
        self,
        collection: str,
        query: Sequence[float],
        top_k: int = 5,
        filter: dict | None = None,
    ) -> list[SearchHit]:
        col = self._cols[collection]
        scored: list[SearchHit] = []
        q = list(query)
        q_norm = math.sqrt(sum(x * x for x in q)) or 1.0
        for _id, vec, pl in zip(col.ids, col.vectors, col.payloads, strict=True):
            if filter and not all(pl.get(k) == v for k, v in filter.items()):
                continue
            v_norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            dot = sum(a * b for a, b in zip(q, vec, strict=True))
            scored.append(SearchHit(id=_id, score=dot / (q_norm * v_norm), payload=pl))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]

    # ------------------------------------------------------------
    # 간단 BM25 (Okapi BM25, k1=1.5, b=0.75) — payload["text"] 기반
    # ------------------------------------------------------------
    def _bm25_scores(
        self,
        col: _Collection,
        query_text: str,
        filter: dict | None,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> dict[str, float]:
        """필터를 통과한 문서 전체에 대해 BM25 점수를 계산하여 {id: score} 반환.

        - 토큰: `\\w+` 매칭 + 소문자. 한글도 \\w에 포함.
        - df / avgdl는 (filter 통과) 코퍼스 기준으로 매 호출 시 계산.
        - 토큰화된 텍스트가 비어 있는 문서는 무시.
        """
        q_tokens = _tokenize(query_text)
        if not q_tokens:
            return {}

        # 1) 후보 인덱스 + tokenized texts 수집
        docs: list[tuple[str, list[str]]] = []
        for _id, pl in zip(col.ids, col.payloads, strict=True):
            if filter and not all(pl.get(k) == v for k, v in filter.items()):
                continue
            text = pl.get("text") or ""
            docs.append((_id, _tokenize(text)))

        if not docs:
            return {}

        # 2) df / avgdl
        doc_lens = [len(toks) for _, toks in docs]
        n = len(docs)
        avgdl = (sum(doc_lens) / n) if n else 0.0

        df: dict[str, int] = {}
        for _, toks in docs:
            for t in set(toks):
                df[t] = df.get(t, 0) + 1

        # 3) BM25
        scores: dict[str, float] = {}
        q_unique = set(q_tokens)
        for (doc_id, toks), dl in zip(docs, doc_lens, strict=True):
            if not toks:
                scores[doc_id] = 0.0
                continue
            # term frequencies in doc
            tf: dict[str, int] = {}
            for t in toks:
                if t in q_unique:
                    tf[t] = tf.get(t, 0) + 1
            score = 0.0
            for t in q_unique:
                f = tf.get(t, 0)
                if f == 0:
                    continue
                df_t = df.get(t, 0)
                # +1 smoothing — df_t == 0 방지 (q_unique는 docs에 있을 때만 의미)
                idf = math.log(1.0 + (n - df_t + 0.5) / (df_t + 0.5))
                denom = f + k1 * (1.0 - b + b * dl / (avgdl or 1.0))
                score += idf * (f * (k1 + 1.0)) / (denom or 1.0)
            scores[doc_id] = score
        return scores

    def search_hybrid(
        self,
        collection: str,
        query_text: str | None,
        query_vec: Sequence[float],
        top_k: int = 5,
        filter: dict | None = None,
        *,
        rrf_constant: int = 60,
        hybrid_alpha: float | None = None,
        **_kwargs: object,
    ) -> list[SearchHit]:
        """BM25 + dense cosine 하이브리드.

        - query_text가 None/빈 문자열이거나 토큰화 결과가 0이면 vec-only로 폴백
          하고 RuntimeWarning을 발생시킨다 (기존 폴리필 동작 유지).
        - 그렇지 않으면 BM25 (token-overlap 기반 Okapi BM25) + cosine을
          결합한다.
          * `hybrid_alpha`가 주어지면 단순 가중합(min-max 정규화):
            score = alpha * cos_norm + (1-alpha) * bm25_norm
          * 그 외에는 RRF (Reciprocal Rank Fusion, k=rrf_constant).
        - 진짜 Lucene 토크나이저·언어 analyzer가 필요하면 EsStore를 쓸 것.
        """
        col = self._cols[collection]

        q_tokens = _tokenize(query_text or "")
        if not q_tokens:
            warnings.warn(
                "InMemoryStore.search_hybrid: query_text 없음/공백 → dense-only로 폴백. "
                "진짜 하이브리드 결과가 필요하면 EsStore를 사용하거나 "
                "isinstance(vs, HybridVectorStore)로 사전 분기하세요.",
                RuntimeWarning,
                stacklevel=2,
            )
            return self.search(collection, query_vec, top_k=top_k, filter=filter)

        # 1) dense cosine — 필터 통과 전체에 대해 점수 계산
        q = list(query_vec)
        q_norm = math.sqrt(sum(x * x for x in q)) or 1.0
        cos_scores: dict[str, float] = {}
        payloads_by_id: dict[str, dict] = {}
        for _id, vec, pl in zip(col.ids, col.vectors, col.payloads, strict=True):
            if filter and not all(pl.get(k) == v for k, v in filter.items()):
                continue
            v_norm = math.sqrt(sum(x * x for x in vec)) or 1.0
            dot = sum(a * b for a, b in zip(q, vec, strict=True))
            cos_scores[_id] = dot / (q_norm * v_norm)
            payloads_by_id[_id] = pl

        # 2) BM25
        bm25_scores = self._bm25_scores(col, query_text or "", filter)

        if not cos_scores and not bm25_scores:
            return []

        candidate_ids = set(cos_scores) | set(bm25_scores)

        # 3) 결합
        if hybrid_alpha is not None:
            # 단순 가중합 (min-max 정규화)
            combined = {
                _id: (
                    hybrid_alpha * _minmax(cos_scores.get(_id, 0.0), cos_scores)
                    + (1.0 - hybrid_alpha) * _minmax(bm25_scores.get(_id, 0.0), bm25_scores)
                )
                for _id in candidate_ids
            }
        else:
            # RRF
            cos_rank = _rank_map(cos_scores)
            bm25_rank = _rank_map(bm25_scores)
            combined = {}
            for _id in candidate_ids:
                rrf = 0.0
                if _id in cos_rank:
                    rrf += 1.0 / (rrf_constant + cos_rank[_id])
                if _id in bm25_rank:
                    rrf += 1.0 / (rrf_constant + bm25_rank[_id])
                combined[_id] = rrf

        # 4) 정렬 + payload 복원
        ordered = sorted(combined.items(), key=lambda kv: kv[1], reverse=True)[:top_k]
        return [
            SearchHit(id=_id, score=score, payload=payloads_by_id.get(_id, {}))
            for _id, score in ordered
        ]

    def count(self, collection: str) -> int:
        return len(self._cols[collection].ids) if collection in self._cols else 0


def _rank_map(scores: dict[str, float]) -> dict[str, int]:
    """점수 내림차순으로 1-based 랭크 매핑. 점수 0(또는 음수)는 제외."""
    if not scores:
        return {}
    ordered = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return {_id: i + 1 for i, (_id, s) in enumerate(ordered) if s > 0}


def _minmax(value: float, scores: dict[str, float]) -> float:
    """min-max 정규화. 모든 점수가 같으면 0.5 반환."""
    if not scores:
        return 0.0
    lo = min(scores.values())
    hi = max(scores.values())
    if hi <= lo:
        return 0.5
    return (value - lo) / (hi - lo)
