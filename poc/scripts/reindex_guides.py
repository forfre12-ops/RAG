"""합성 코퍼스 ES 재색인 — `docs` 컬렉션 (/answer RAG 검색 대상).

윈도우 재설치로 ES named volume(esdata)이 소실되어, 살아있는 합성 데이터셋을
다시 색인한다. /answer 의 retrieval facade 는 `settings.rag_default_collection`
(기본 "docs") 을 BM25+dense 하이브리드로 검색하므로 동일 컬렉션에 적재한다.

각 합성 문서(body)를 청크 분할 → BGE-M3 임베딩 → docs 에 bulk upsert.
payload 는 es_store.search_hybrid 가 BM25용으로 쓰는 `text` 필드와
RagContextHit 가 쓰는 `doc_id`/`grade` 를 포함한다.

사용 (api 컨테이너 안, datasets 마운트 필요):
    python scripts/reindex_guides.py DATASET_DIR [LIMIT]
예:
    python scripts/reindex_guides.py datasets/synthetic_qwen3 200
    python scripts/reindex_guides.py datasets/synthetic_5k 0   # 전체
"""
from __future__ import annotations

import datetime as dt
import glob
import json
import sys
import time

from lloydk.adapters.embedding import build_embedder
from lloydk.adapters.vectorstore import build_store
from lloydk.config import settings
from lloydk.modules.m2_preprocess import split as chunk_split


def _load_docs(dataset_dir: str, limit: int) -> list[dict]:
    files = sorted(glob.glob(f"{dataset_dir}/*.json"))
    if limit > 0:
        files = files[:limit]
    docs = []
    for f in files:
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        body = d.get("body") or d.get("content") or ""
        if not body.strip():
            continue
        docs.append(
            {
                "doc_id": d.get("synth_id") or d.get("doc_id") or f,
                "grade": d.get("target_grade") or d.get("label"),
                "title": d.get("title") or "",
                "domain": d.get("domain"),
                "body": body,
            }
        )
    return docs


def main() -> int:
    dataset_dir = sys.argv[1] if len(sys.argv) > 1 else "datasets/synthetic_qwen3"
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else 0

    collection = getattr(settings, "rag_default_collection", "docs")
    docs = _load_docs(dataset_dir, limit)
    if not docs:
        print(f"[reindex] no usable docs under {dataset_dir}", flush=True)
        return 1
    print(
        f"[reindex] dir={dataset_dir} docs={len(docs)} -> collection={collection}",
        flush=True,
    )

    store = build_store()
    embedder = build_embedder()
    chunk_size = settings.rag_index_chunk_size
    chunk_overlap = settings.rag_index_chunk_overlap

    # 컬렉션 보장 (dim 은 임베더 차원). 이미 있으면 skip.
    store.ensure_collection(collection, embedder.dim)

    now = dt.datetime.now(dt.timezone.utc).isoformat()
    t0 = time.time()
    total_chunks = 0
    batch_ids: list[str] = []
    batch_vecs: list[list[float]] = []
    batch_payloads: list[dict] = []
    BATCH = 64

    def _flush() -> int:
        nonlocal batch_ids, batch_vecs, batch_payloads
        if not batch_ids:
            return 0
        n = store.upsert(collection, batch_ids, batch_vecs, batch_payloads)
        batch_ids, batch_vecs, batch_payloads = [], [], []
        return int(n)

    for di, doc in enumerate(docs):
        chunks = chunk_split(doc["body"], size=chunk_size, overlap=chunk_overlap)
        if not chunks:
            continue
        texts = [c.text for c in chunks]
        emb = embedder.embed(texts)
        for ci, (text, vec) in enumerate(zip(texts, emb.vectors, strict=True)):
            batch_ids.append(f"{doc['doc_id']}:c{ci}")
            batch_vecs.append(vec)
            batch_payloads.append(
                {
                    "id": f"{doc['doc_id']}:c{ci}",
                    "doc_id": doc["doc_id"],
                    "chunk_idx": ci,
                    "tenant_id": "_global",
                    "grade": doc["grade"],
                    "doc_type": doc.get("domain"),
                    "version": "reindex-2026-05-30",
                    "created_at": now,
                    "text": text,
                }
            )
            total_chunks += 1
            if len(batch_ids) >= BATCH:
                _flush()
        if (di + 1) % 200 == 0:
            dt_now = time.time() - t0
            print(
                f"[reindex] {di + 1}/{len(docs)} docs, {total_chunks} chunks, "
                f"{(di + 1) / dt_now:.1f} docs/s",
                flush=True,
            )

    _flush()
    elapsed = time.time() - t0
    print(
        f"[reindex] DONE docs={len(docs)} chunks={total_chunks} "
        f"elapsed={elapsed:.1f}s rate={len(docs) / elapsed:.1f} docs/s "
        f"count={store.count(collection)}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
