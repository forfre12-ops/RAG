"""RAG 코퍼스 → ES docs 인덱스 적재. 여러 디렉터리 지원."""
import json
import os
import sys
import uuid
from pathlib import Path

os.chdir(Path(__file__).resolve().parent.parent)
sys.path.insert(0, "src")
from dotenv import load_dotenv
load_dotenv(".env")

from lloydk.adapters.embedding import build_embedder
from lloydk.adapters.vectorstore import build_store
from lloydk.modules.m2_preprocess.pipeline import PreprocessPipeline

COLLECTION = "docs"
BATCH_SIZE = 16

CORPUS_DIRS = [
    Path("datasets/rag_corpus_v2"),
    Path("datasets/rag_corpus_qa"),
]


def main():
    all_files = []
    for d in CORPUS_DIRS:
        if d.exists():
            all_files.extend(sorted(d.glob("*.json")))

    if not all_files:
        print("[ERROR] 코퍼스 파일 없음")
        return 1

    store    = build_store()
    embedder = build_embedder()
    pipe     = PreprocessPipeline()

    dim = getattr(embedder, "dim", 1024)
    store.ensure_collection(COLLECTION, dim=dim)

    print(f"총 {len(all_files)}문서 → ES '{COLLECTION}' 적재 시작")

    batch_ids, batch_texts, batch_payloads = [], [], []
    total_chunks = 0

    for fi, f in enumerate(all_files):
        d    = json.loads(f.read_text("utf-8"))
        body = d.get("body", "").strip()
        if not body:
            continue

        clean  = pipe.run_text(body)
        chunks = pipe.chunk(clean) if clean else []
        if not chunks:
            chunks = [type("C", (), {"text": clean})()]

        for ci, chunk in enumerate(chunks):
            text = getattr(chunk, "text", None) or (chunk.get("text", clean) if isinstance(chunk, dict) else clean)
            if not text or not text.strip():
                continue
            batch_ids.append(str(uuid.uuid4()))
            batch_texts.append(text)
            batch_payloads.append({
                "text":        text,
                "grade":       d.get("target_grade"),
                "domain":      d.get("domain"),
                "doc_type":    d.get("document_type", ""),
                "title":       d.get("title", ""),
                "chunk_idx":   ci,
                "source_file": f.name,
                "source_dir":  f.parent.name,
            })

            if len(batch_texts) >= BATCH_SIZE:
                total_chunks += _flush(store, embedder, batch_ids, batch_texts, batch_payloads, COLLECTION)
                batch_ids, batch_texts, batch_payloads = [], [], []

        if (fi + 1) % 100 == 0:
            print(f"  {fi+1}/{len(all_files)}문서 ({total_chunks}청크)...")

    if batch_texts:
        total_chunks += _flush(store, embedder, batch_ids, batch_texts, batch_payloads, COLLECTION)

    print(f"\n완료: {len(all_files)}문서 → {total_chunks}청크 → ES '{COLLECTION}'")
    return 0


def _flush(store, embedder, ids, texts, payloads, collection):
    result  = embedder.embed(texts)
    vectors = getattr(result, "vectors", None) or result
    n = store.upsert(
        collection=collection,
        ids=ids,
        vectors=[list(v) for v in vectors],
        payloads=payloads,
    )
    return n


if __name__ == "__main__":
    raise SystemExit(main())
