"""m6_evaluation.retrieval_metrics — Recall@k · MRR · nDCG 단위 테스트."""

from __future__ import annotations

import math

import pytest

from lloydk.modules.m6_evaluation import (
    RetrievalMetricsResult,
    compute_retrieval_metrics_from_arrays,
)
from lloydk.modules.m6_evaluation.retrieval_metrics import (
    compute_retrieval_metrics_from_db,
)


# ------------------------------------------------------------
# compute_retrieval_metrics_from_arrays
# ------------------------------------------------------------


def test_perfect_retrieval_all_relevant_first_position():
    preds = [["a", "b", "c"], ["x", "y", "z"]]
    rels = [{"a"}, {"x"}]
    r = compute_retrieval_metrics_from_arrays(preds, rels, top_k=3)

    assert r.sample_count == 2
    assert r.recall_at_k == 1.0
    assert r.mrr == 1.0  # 양쪽 1위
    assert r.ndcg_at_k == 1.0
    # precision: 1/3 each → mean 1/3
    assert r.precision_at_k == pytest.approx(1.0 / 3.0)


def test_miss_returns_zero_for_query():
    preds = [["a", "b", "c"]]
    rels = [{"z"}]
    r = compute_retrieval_metrics_from_arrays(preds, rels, top_k=3)

    assert r.sample_count == 1
    assert r.recall_at_k == 0.0
    assert r.mrr == 0.0
    assert r.ndcg_at_k == 0.0


def test_mrr_second_position_is_half():
    preds = [["wrong", "right", "other"]]
    rels = [{"right"}]
    r = compute_retrieval_metrics_from_arrays(preds, rels, top_k=3)

    assert r.mrr == pytest.approx(0.5)
    # nDCG: 1/log2(3) / 1.0 (IDCG with 1 relevant)
    assert r.ndcg_at_k == pytest.approx(1.0 / math.log2(3))


def test_top_k_truncation():
    # relevant은 4번째에 있지만 top_k=3이라 못 잡음
    preds = [["a", "b", "c", "right"]]
    rels = [{"right"}]
    r = compute_retrieval_metrics_from_arrays(preds, rels, top_k=3)

    assert r.recall_at_k == 0.0
    assert r.mrr == 0.0


def test_multiple_relevants_partial_hit():
    # 2개 relevant 중 1개만 잡음 → recall 0.5
    preds = [["a", "irrelevant", "c"]]
    rels = [{"a", "x"}]
    r = compute_retrieval_metrics_from_arrays(preds, rels, top_k=3)

    assert r.recall_at_k == pytest.approx(0.5)
    assert r.precision_at_k == pytest.approx(1.0 / 3.0)
    assert r.mrr == 1.0
    # nDCG: dcg = 1/log2(2) = 1, idcg = 1 + 1/log2(3)
    expected = 1.0 / (1.0 + 1.0 / math.log2(3))
    assert r.ndcg_at_k == pytest.approx(expected)


def test_empty_relevants_skipped_from_sample():
    preds = [["a", "b"], ["c", "d"]]
    rels = [{"a"}, set()]  # 두 번째 쿼리는 relevant 없음
    r = compute_retrieval_metrics_from_arrays(preds, rels, top_k=2)

    assert r.sample_count == 1
    assert r.skipped_count == 1
    assert r.recall_at_k == 1.0


def test_empty_inputs_returns_zeros():
    r = compute_retrieval_metrics_from_arrays([], [], top_k=5)
    assert r.sample_count == 0
    assert r.recall_at_k == 0.0


def test_per_query_payload_includes_labels():
    preds = [["a", "b"], ["x", "y"]]
    rels = [{"a"}, {"y"}]
    qids = ["q-1", "q-2"]
    r = compute_retrieval_metrics_from_arrays(preds, rels, top_k=2, query_ids=qids)

    assert len(r.per_query) == 2
    by_id = {row["query_id"]: row for row in r.per_query}
    assert by_id["q-1"]["mrr"] == 1.0
    assert by_id["q-2"]["mrr"] == pytest.approx(0.5)


def test_to_dict_round_trip():
    r = compute_retrieval_metrics_from_arrays([["a"]], [{"a"}], top_k=1)
    d = r.to_dict()
    assert d["sample_count"] == 1
    assert d["recall_at_k"] == 1.0
    assert isinstance(d["per_query"], list)


def test_result_dataclass_type():
    r = compute_retrieval_metrics_from_arrays([["a"]], [{"a"}], top_k=1)
    assert isinstance(r, RetrievalMetricsResult)


# ------------------------------------------------------------
# compute_retrieval_metrics_from_db
# ------------------------------------------------------------


def test_from_db_returns_none_when_relevants_missing():
    # relevants 인자 없으면 즉시 None (DB 접근도 하지 않음)
    assert compute_retrieval_metrics_from_db() is None
    assert compute_retrieval_metrics_from_db(relevants_by_classification={}) is None
