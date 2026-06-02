"""P2 쿼리확장 실측 — rule/llm/hybrid vs baseline(no expansion) Recall@5.

기존 p2_cached_es 인덱스(KURE-v1 + ES, oss_corpus chunk) 재사용.
retrieval_gold 80쿼리에 대해 method별 expand_then_search → doc_id Recall@5.
매칭 로직은 p2_compare_embeddings와 동일(payload.doc_id/base_doc_id ∈ relevant_set).

baseline(none) = search_hybrid 직접. p2_compare 기준 Recall@5≈0.925.
LLM 확장은 settings.llm_provider(Ollama qwen3:14b)로 expand_llm 호출 → 쿼리당 수초.

산출: reports/p2_query_expansion_eval.md (reports는 gitignore — 콘솔에도 출력).
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

from lloydk.adapters.embedding import build_embedder
from lloydk.adapters.vectorstore import build_store
from lloydk.services.retrieval import expand_then_search

COLLECTION = "p2_cached_es"
GOLD = Path("datasets/gold_real/retrieval_gold.jsonl")
TOP_K = 5
METHODS = ["none", "rule", "llm", "hybrid"]


def _doc_match(results, relevant_set: set[str]) -> bool:
    for r in results[:TOP_K]:
        did = str(r.payload.get("doc_id", ""))
        bid = str(r.payload.get("base_doc_id", ""))
        if (
            did in relevant_set
            or bid in relevant_set
            or any(did.startswith(rel + "-c") for rel in relevant_set)
            or any(did.startswith(rel + ":c") for rel in relevant_set)
        ):
            return True
    return False


def main() -> int:
    emb = build_embedder()
    store = build_store()

    def encode(t: str):
        r = emb.embed([t])
        v = getattr(r, "vectors", None) or r
        return v[0]

    def encode_batch(ts: list[str]):
        if not ts:
            return []
        r = emb.embed(ts)
        v = getattr(r, "vectors", None) or r
        return list(v)

    queries = [json.loads(line) for line in GOLD.open(encoding="utf-8")]
    print(f"[p2-qe] queries={len(queries)} collection={COLLECTION} top_k={TOP_K}", flush=True)

    rows = []
    for method in METHODS:
        hits = 0
        t0 = time.perf_counter()
        for q in queries:
            exp = q.get("expected_doc_id")
            rel = q.get("relevant_doc_ids") or ([exp] if exp else [])
            relevant_set = {str(x) for x in rel if x}
            if method == "none":
                qv = encode(q["text"])
                res = store.search_hybrid(COLLECTION, q["text"], qv, top_k=TOP_K)
            else:
                res = expand_then_search(
                    store=store,
                    collection=COLLECTION,
                    query_text=q["text"],
                    encode=encode,
                    encode_batch=encode_batch,
                    method=method,
                    top_k=TOP_K,
                    use_reranker=False,
                )
            if _doc_match(res, relevant_set):
                hits += 1
        recall = hits / len(queries) if queries else 0.0
        elapsed = time.perf_counter() - t0
        rows.append((method, recall, elapsed))
        print(f"[p2-qe] {method}: Recall@{TOP_K}={recall:.4f}  ({elapsed:.1f}s)", flush=True)

    base = next((r for m, r, _ in rows if m == "none"), 0.0) if isinstance(rows[0], tuple) else 0.0
    lines = [
        "# P2 Query Expansion Eval",
        "",
        f"- collection: `{COLLECTION}` (KURE-v1 + ES hybrid, oss_corpus chunk)",
        f"- queries: {len(queries)} (retrieval_gold)",
        f"- top_k: {TOP_K}",
        "",
        "| method | Recall@5 | Δ vs baseline | elapsed |",
        "|---|---:|---:|---:|",
    ]
    for m, r, e in rows:
        delta = "—" if m == "none" else f"{(r - base) * 100:+.1f}pp"
        lines.append(f"| {m} | {r:.4f} | {delta} | {e:.1f}s |")
    out = Path("reports/p2_query_expansion_eval.md")
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"[p2-qe] report -> {out}", flush=True)
    print("P2_QE_DONE", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
