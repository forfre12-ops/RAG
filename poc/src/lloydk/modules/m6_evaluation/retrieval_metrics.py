"""RAG 검색 품질 평가 — Recall@k · Precision@k · MRR · nDCG@k.

분류 평가(metrics.py)와 동일한 인터페이스 — from_arrays(순수) + from_db.

배경:
- 기존 src/lloydk/modules/m6_evaluation/는 분류 KPI(F1·FNR)만 다뤘다.
- RAG 검색 품질은 scripts/p2_compare_embeddings.py에 박혀 있어 운영 중 재사용
  불가. ClassifyService가 영속화한 ClassificationEvidence(evidence_type='rag_context')
  를 활용한 in-system 평가가 빠져 있었다.

지표:
- recall_at_k: |retrieved ∩ relevant| / |relevant|. 1개 이상 relevant 없는 쿼리는 제외.
- precision_at_k: |retrieved ∩ relevant| / k.
- mrr: 첫 relevant의 1/rank. 못 찾으면 0.
- ndcg_at_k: binary gain 기준 — sum(rel_i / log2(i+2)) / IDCG. ties는 입력 순서.
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from lloydk.db import session_scope
from lloydk.db.models import Classification, ClassificationEvidence


@dataclass
class RetrievalMetricsResult:
    sample_count: int                       # 유효 쿼리 수 (relevant 1건 이상)
    top_k: int
    recall_at_k: float
    precision_at_k: float
    mrr: float
    ndcg_at_k: float
    per_query: list[dict] = field(default_factory=list)
    skipped_count: int = 0                  # relevant 비어 있어 제외된 쿼리 수

    def to_dict(self) -> dict:
        return {
            "sample_count": self.sample_count,
            "top_k": self.top_k,
            "recall_at_k": self.recall_at_k,
            "precision_at_k": self.precision_at_k,
            "mrr": self.mrr,
            "ndcg_at_k": self.ndcg_at_k,
            "per_query": self.per_query,
            "skipped_count": self.skipped_count,
        }


def compute_retrieval_metrics_from_arrays(
    predictions: Sequence[Sequence[str]],
    relevants: Sequence[Iterable[str]],
    *,
    top_k: int = 5,
    query_ids: Sequence[str] | None = None,
) -> RetrievalMetricsResult:
    """순수 함수 평가.

    Args:
        predictions: 쿼리별 검색 결과 id 시퀀스. 길이가 top_k보다 짧으면 그대로 사용.
        relevants:   쿼리별 정답 id 집합. 비어 있으면 해당 쿼리는 skip.
        top_k:       평가 cutoff. predictions가 더 길어도 앞 top_k만 본다.
        query_ids:   per_query 보고용 라벨. None이면 인덱스 사용.

    relevant가 비어 있는 쿼리는 sample_count에서 제외 + skipped_count로 분리 보고.
    predictions와 relevants 길이가 다르면 짧은 쪽까지만.
    """
    n = min(len(predictions), len(relevants))
    if n == 0 or top_k <= 0:
        return _empty(top_k)

    per_query: list[dict] = []
    recall_sum = precision_sum = mrr_sum = ndcg_sum = 0.0
    valid = 0
    skipped = 0

    for i in range(n):
        relevant_set = {str(x) for x in relevants[i] if x is not None}
        if not relevant_set:
            skipped += 1
            continue

        ranked = []
        seen = set()
        for x in predictions[i]:
            doc_id = str(x)
            if doc_id in seen:
                continue
            ranked.append(doc_id)
            seen.add(doc_id)
            if len(ranked) >= top_k:
                break
        hit_set = relevant_set.intersection(ranked)

        recall = len(hit_set) / len(relevant_set)
        precision = len(hit_set) / top_k

        mrr = 0.0
        for rank, doc_id in enumerate(ranked, start=1):
            if doc_id in relevant_set:
                mrr = 1.0 / rank
                break

        dcg = 0.0
        for rank, doc_id in enumerate(ranked, start=1):
            if doc_id in relevant_set:
                dcg += 1.0 / math.log2(rank + 1)
        ideal_hits = min(len(relevant_set), top_k)
        idcg = sum(1.0 / math.log2(r + 1) for r in range(1, ideal_hits + 1))
        ndcg = (dcg / idcg) if idcg > 0 else 0.0

        recall_sum += recall
        precision_sum += precision
        mrr_sum += mrr
        ndcg_sum += ndcg
        valid += 1

        per_query.append({
            "query_id": str(query_ids[i]) if query_ids and i < len(query_ids) else str(i),
            "retrieved_count": len(ranked),
            "relevant_count": len(relevant_set),
            "hits": len(hit_set),
            "recall": recall,
            "precision": precision,
            "mrr": mrr,
            "ndcg": ndcg,
        })

    if valid == 0:
        return RetrievalMetricsResult(
            sample_count=0,
            top_k=top_k,
            recall_at_k=0.0,
            precision_at_k=0.0,
            mrr=0.0,
            ndcg_at_k=0.0,
            per_query=per_query,
            skipped_count=skipped,
        )

    return RetrievalMetricsResult(
        sample_count=valid,
        top_k=top_k,
        recall_at_k=recall_sum / valid,
        precision_at_k=precision_sum / valid,
        mrr=mrr_sum / valid,
        ndcg_at_k=ndcg_sum / valid,
        per_query=per_query,
        skipped_count=skipped,
    )


def compute_retrieval_metrics_from_db(
    *,
    model_version: str | None = None,
    tenant_id: str | None = None,
    top_k: int = 5,
    relevants_by_classification: dict[uuid.UUID, Iterable[str]] | None = None,
) -> RetrievalMetricsResult | None:
    """ClassificationEvidence(evidence_type='rag_context')에서 검색 결과를 모아 평가.

    정답 집합은 외부에서 주입(relevants_by_classification) — KOIPA 운영에서는
    검수자가 확정한 doc_id 집합을 별도 테이블/JSON으로 관리한다 가정.

    Args:
        model_version: classifications 필터. None이면 모든 버전.
        tenant_id:     tenant 필터.
        top_k:         평가 cutoff. evidence는 contribution(rag_similarity) 내림차순.
        relevants_by_classification:
                       classification_id → relevant doc_id 집합.
                       빠진 분류는 evaluation skip(sample에서 제외).

    Returns:
        None: DB 미가용 또는 RAG evidence 0건.
    """
    if not relevants_by_classification:
        return None

    try:
        with session_scope() as db:
            pairs = _fetch_rag_evidence(
                db,
                model_version=model_version,
                tenant_id=tenant_id,
                top_k=top_k,
            )
    except SQLAlchemyError:
        return None

    if not pairs:
        return None

    preds: list[list[str]] = []
    rels: list[set[str]] = []
    qids: list[str] = []
    for cls_id, retrieved in pairs:
        rel = relevants_by_classification.get(cls_id)
        if rel is None:
            continue
        preds.append(retrieved)
        rels.append({str(x) for x in rel})
        qids.append(str(cls_id))

    if not preds:
        return None

    return compute_retrieval_metrics_from_arrays(
        preds, rels, top_k=top_k, query_ids=qids,
    )


# ------------------------------------------------------------
# internals
# ------------------------------------------------------------


def _fetch_rag_evidence(
    db: Session,
    *,
    model_version: str | None,
    tenant_id: str | None,
    top_k: int,
) -> list[tuple[uuid.UUID, list[str]]]:
    """분류별 RAG retrieved 문서 id 리스트 (rag_similarity 내림차순, top_k 컷).

    rag_ref_doc_id가 NULL인 evidence는 제외 (RAG가 아닌 keyword_match 등 섞일 수 있음).
    """
    stmt = select(Classification.classification_id)
    if model_version is not None:
        stmt = stmt.where(Classification.model_version == model_version)
    if tenant_id is not None:
        stmt = stmt.where(Classification.tenant_id == tenant_id)
    cls_ids = [row for row in db.execute(stmt).scalars()]
    if not cls_ids:
        return []

    ev_stmt = (
        select(
            ClassificationEvidence.classification_id,
            ClassificationEvidence.rag_ref_doc_id,
            ClassificationEvidence.rag_similarity,
        )
        .where(ClassificationEvidence.classification_id.in_(cls_ids))
        .where(ClassificationEvidence.evidence_type == "rag_context")
        .where(ClassificationEvidence.rag_ref_doc_id.is_not(None))
        .order_by(
            ClassificationEvidence.classification_id,
            ClassificationEvidence.rag_similarity.desc().nulls_last(),
        )
    )

    by_cls: dict[uuid.UUID, list[str]] = {}
    for cls_id, ref_doc, _sim in db.execute(ev_stmt):
        bucket = by_cls.setdefault(cls_id, [])
        if len(bucket) < top_k:
            bucket.append(str(ref_doc))

    return list(by_cls.items())


def _empty(top_k: int) -> RetrievalMetricsResult:
    return RetrievalMetricsResult(
        sample_count=0,
        top_k=top_k,
        recall_at_k=0.0,
        precision_at_k=0.0,
        mrr=0.0,
        ndcg_at_k=0.0,
        per_query=[],
        skipped_count=0,
    )
