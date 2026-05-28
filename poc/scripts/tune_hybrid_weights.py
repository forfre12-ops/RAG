"""A2: 하이브리드 검색 가중치 튜닝 — BM25 : dense 비율 grid search.

입력:
- 평가쿼리셋 JSONL: {"query": str, "relevant_ids": [doc_id, ...]}
- ES 인덱스 alias / 컬렉션 명

출력:
- 가중치(α=BM25 weight, 1-α=dense weight) 별 Recall@5 / nDCG@10 / MRR 표
- 최적 가중치 추천 (Recall@5 기준)

사용:
  python scripts/tune_hybrid_weights.py \
      --eval-set datasets/eval/queries_v1.jsonl \
      --collection lloydk-docs \
      --steps 11 \
      --top-k 5

NOTE: 본 스크립트는 ES + dense embedding이 실재해야 의미 있는 측정.
dryrun 환경(InMemory + Hash)에서는 placeholder 출력만 검증 가능.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Iterable


def _load_eval_set(path: Path) -> list[dict]:
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


def _recall_at_k(predicted_ids: list[str], relevant_ids: list[str], k: int) -> float:
    if not relevant_ids:
        return 0.0
    top = set(predicted_ids[:k])
    hit = sum(1 for r in relevant_ids if r in top)
    return hit / len(relevant_ids)


def _mrr(predicted_ids: list[str], relevant_ids: list[str]) -> float:
    rel_set = set(relevant_ids)
    for i, pid in enumerate(predicted_ids, 1):
        if pid in rel_set:
            return 1.0 / i
    return 0.0


def _ndcg_at_k(predicted_ids: list[str], relevant_ids: list[str], k: int) -> float:
    rel_set = set(relevant_ids)
    dcg = 0.0
    for i, pid in enumerate(predicted_ids[:k], 1):
        if pid in rel_set:
            dcg += 1.0 / math.log2(i + 1)
    ideal = sum(1.0 / math.log2(i + 1) for i in range(1, min(len(relevant_ids), k) + 1))
    return (dcg / ideal) if ideal > 0 else 0.0


def _grid_alphas(steps: int) -> list[float]:
    if steps < 2:
        return [0.5]
    return [round(i / (steps - 1), 4) for i in range(steps)]


def _run_search_stub(query: str, alpha: float, collection: str, top_k: int) -> list[str]:
    """A2 스텁 — 실 ES 어댑터 연동은 운영 환경에서 활성화.

    실 구현 예시(주석):
        from lloydk.adapters.vectorstore import EsStore
        from lloydk.adapters.embedding import get_embedder
        store = EsStore(...)
        emb = get_embedder()
        vec = emb.embed([query])[0]
        # alpha: BM25 가중치, 1-alpha: dense 가중치 (RRF 또는 weighted sum)
        hits = store.search_hybrid(collection, query, vec, top_k=top_k,
                                    bm25_weight=alpha, dense_weight=1-alpha)
        return [h.id for h in hits]
    """
    # 결정론적 placeholder — alpha에 따라 약간씩 다른 id 순서를 만들어
    # 스크립트 자체는 dryrun에서도 수행 가능
    base = [f"doc_{i:03d}" for i in range(top_k * 4)]
    shift = int(alpha * top_k) % len(base)
    return base[shift:shift + top_k] + base[:max(0, top_k - (len(base) - shift))]


def evaluate(
    eval_set: Iterable[dict],
    *,
    collection: str,
    alphas: list[float],
    top_k: int,
    runner=_run_search_stub,
) -> list[dict]:
    rows = list(eval_set)
    out: list[dict] = []
    for a in alphas:
        agg_r5 = 0.0
        agg_mrr = 0.0
        agg_ndcg = 0.0
        for row in rows:
            q = row["query"]
            rel = list(row.get("relevant_ids", []))
            preds = runner(q, a, collection, top_k)
            agg_r5 += _recall_at_k(preds, rel, top_k)
            agg_mrr += _mrr(preds, rel)
            agg_ndcg += _ndcg_at_k(preds, rel, top_k)
        n = max(1, len(rows))
        out.append({
            "alpha_bm25": a,
            "alpha_dense": round(1.0 - a, 4),
            f"recall@{top_k}": round(agg_r5 / n, 4),
            "mrr": round(agg_mrr / n, 4),
            f"ndcg@{top_k}": round(agg_ndcg / n, 4),
            "n_queries": len(rows),
        })
    return out


def best_alpha(results: list[dict], metric: str) -> dict:
    return max(results, key=lambda r: r.get(metric, 0.0))


def render_markdown(results: list[dict], top_k: int, best: dict) -> str:
    head = (
        f"| α_BM25 | α_dense | Recall@{top_k} | MRR | nDCG@{top_k} | N |\n"
        f"|--------|---------|----------:|----:|---------:|--:|"
    )
    rows = "\n".join(
        f"| {r['alpha_bm25']:.2f} | {r['alpha_dense']:.2f} | {r[f'recall@{top_k}']:.3f} | {r['mrr']:.3f} | {r[f'ndcg@{top_k}']:.3f} | {r['n_queries']} |"
        for r in results
    )
    foot = (
        f"\n\n**Best (Recall@{top_k})**: α_BM25={best['alpha_bm25']:.2f}, "
        f"α_dense={best['alpha_dense']:.2f}, Recall={best[f'recall@{top_k}']:.3f}"
    )
    return head + "\n" + rows + foot


def main() -> int:
    parser = argparse.ArgumentParser(description="A2: hybrid 가중치 grid search")
    parser.add_argument("--eval-set", type=Path, required=False,
                        help="평가셋 JSONL ({query, relevant_ids})")
    parser.add_argument("--collection", default="lloydk-docs")
    parser.add_argument("--steps", type=int, default=11, help="α 그리드 step 수 (default 11 → 0.0~1.0 by 0.1)")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output-json", type=Path, default=None)
    parser.add_argument("--output-md", type=Path, default=None)
    args = parser.parse_args()

    if args.eval_set and args.eval_set.exists():
        eval_set = _load_eval_set(args.eval_set)
    else:
        # placeholder dryrun — 평가셋 없으면 결정론 dummy 3건
        eval_set = [
            {"query": "M&A 검토안", "relevant_ids": ["doc_001", "doc_004"]},
            {"query": "공개 보도자료", "relevant_ids": ["doc_010"]},
            {"query": "원전 노심 설계", "relevant_ids": ["doc_007", "doc_009"]},
        ]
        print("[WARN] --eval-set 미지정 — 내장 dummy 3건으로 dryrun 수행", file=sys.stderr)

    alphas = _grid_alphas(args.steps)
    results = evaluate(eval_set, collection=args.collection, alphas=alphas, top_k=args.top_k)
    best = best_alpha(results, f"recall@{args.top_k}")

    md = render_markdown(results, args.top_k, best)
    print(md)

    if args.output_json:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps({"results": results, "best": best}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if args.output_md:
        args.output_md.parent.mkdir(parents=True, exist_ok=True)
        args.output_md.write_text(md, encoding="utf-8")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
