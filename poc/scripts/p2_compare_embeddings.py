"""
P2 PoC — KURE-v1 vs BGE-M3 한국어 retrieval 비교.

용도: 가이드 문서 청크에 대한 쿼리 Top-K 검색의 Recall@K, NDCG@K 비교.
입력: 평가셋 JSONL { "query": str, "relevant_doc_ids": [str], "candidates": {doc_id: text} }
출력: reports/p2_embedding_compare.json

사용:
  python scripts/p2_compare_embeddings.py \
    --eval-set datasets/eval/retrieval_eval.jsonl \
    --out reports/p2_embedding_compare.json
"""
import argparse
import json
import math
import time
from pathlib import Path


def ndcg_at_k(rel: list[int], k: int) -> float:
    dcg = sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(rel[:k]))
    ideal = sorted(rel, reverse=True)
    idcg = sum((2 ** r - 1) / math.log2(i + 2) for i, r in enumerate(ideal[:k]))
    return dcg / idcg if idcg > 0 else 0.0


def recall_at_k(retrieved: list[str], relevant: set[str], k: int) -> float:
    if not relevant:
        return 0.0
    hits = sum(1 for d in retrieved[:k] if d in relevant)
    return hits / len(relevant)


def eval_model(model_name: str, queries: list[dict], k_values=(1, 3, 5, 10)) -> dict:
    from sentence_transformers import SentenceTransformer
    import numpy as np

    print(f"\n=== loading {model_name} ===")
    t0 = time.time()
    model = SentenceTransformer(model_name, trust_remote_code=True)
    load_sec = time.time() - t0
    print(f"  loaded in {load_sec:.1f}s")

    metrics: dict = {f"recall@{k}": [] for k in k_values}
    metrics.update({f"ndcg@{k}": [] for k in k_values})
    latencies = []

    for q in queries:
        cand_ids = list(q["candidates"].keys())
        cand_texts = [q["candidates"][i] for i in cand_ids]
        relevant = set(q["relevant_doc_ids"])

        t0 = time.time()
        q_emb = model.encode([q["query"]], normalize_embeddings=True)
        d_emb = model.encode(cand_texts, normalize_embeddings=True, batch_size=32)
        sims = (q_emb @ d_emb.T)[0]
        order = np.argsort(-sims)
        retrieved = [cand_ids[i] for i in order]
        latencies.append((time.time() - t0) * 1000)

        rel_binary = [1 if d in relevant else 0 for d in retrieved]
        for k in k_values:
            metrics[f"recall@{k}"].append(recall_at_k(retrieved, relevant, k))
            metrics[f"ndcg@{k}"].append(ndcg_at_k(rel_binary, k))

    out = {k: sum(v) / len(v) if v else 0.0 for k, v in metrics.items()}
    out["latency_ms_mean"] = sum(latencies) / len(latencies) if latencies else 0.0
    out["load_sec"] = load_sec
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--eval-set", required=True)
    ap.add_argument("--out", default="reports/p2_embedding_compare.json")
    ap.add_argument("--models", nargs="+",
                    default=["nlpai-lab/KURE-v1", "BAAI/bge-m3"])
    args = ap.parse_args()

    queries = []
    for line in Path(args.eval_set).read_text(encoding="utf-8").splitlines():
        if line.strip():
            queries.append(json.loads(line))
    print(f"queries: {len(queries)}")

    results = {}
    for m in args.models:
        results[m] = eval_model(m, queries)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    print("\n" + "=" * 70)
    print(f"{'metric':<20}" + "".join(f"{m[-20:]:<24}" for m in args.models))
    print("=" * 70)
    keys = sorted(set().union(*(r.keys() for r in results.values())))
    for k in keys:
        row = f"{k:<20}"
        for m in args.models:
            v = results[m].get(k)
            row += f"{v:<24.4f}" if isinstance(v, float) else f"{str(v):<24}"
        print(row)
    print(f"\nreport: {args.out}")


if __name__ == "__main__":
    main()
