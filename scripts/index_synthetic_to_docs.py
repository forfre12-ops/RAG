"""B1-2 (5070 Ti 야간 후속): 합성 문서를 ES `docs` 컬렉션에 영구 인덱싱.

용도:
- 데모 콘솔 /demo/ 의 RAG ON 호출에서 rag_hits=0 사고 해소
- 시연 시 발주처에 "citations 3건 검색됨" 결과 노출

대상:
- datasets/synthetic_5k/ (5,000건) + datasets/synthetic_qwen3/ (200건) = 5,200건
- 각 JSON 의 title + body 를 텍스트로 만들어 임베딩 → ES upsert

전제:
- onprem-local 프로파일 (.env), ES 컨테이너 healthy
- EMBEDDING_MODEL=BAAI/bge-m3 (Phase 2 1순위)

사용:
  python scripts/index_synthetic_to_docs.py [--collection docs] [--max 5200]
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "poc" / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", default="docs", help="ES alias/index 이름 (기본 docs)")
    ap.add_argument("--max", type=int, default=None, help="인덱싱 최대 건수 (기본 무제한)")
    ap.add_argument("--batch", type=int, default=32, help="임베딩 배치 사이즈")
    args = ap.parse_args()

    from koipa.adapters.embedding import build_embedder  # noqa: PLC0415
    from koipa.adapters.vectorstore import build_store  # noqa: PLC0415

    print(f"[index] build_embedder + build_store ...", flush=True)
    embedder = build_embedder()
    store = build_store()
    print(f"[index] embedder={embedder.name} dim={embedder.dim}", flush=True)

    store.ensure_collection(args.collection, dim=embedder.dim)

    # 소스 디렉토리 수집
    sources = []
    for p in [_HERE.parent / "poc" / "datasets" / "synthetic_5k",
              _HERE.parent / "poc" / "datasets" / "synthetic_qwen3"]:
        if p.exists():
            files = sorted(p.glob("*.json"))
            sources.append((p.name, files))
            print(f"[index] {p.name}: {len(files)} files", flush=True)

    total_files = sum(len(f) for _, f in sources)
    if args.max:
        total_files = min(total_files, args.max)
    print(f"[index] 인덱싱 대상 {total_files} 건 / collection={args.collection}", flush=True)

    # 배치 인덱싱
    batch_ids, batch_texts, batch_payloads = [], [], []
    indexed = 0
    t0 = time.time()
    last_log = t0

    for src_name, files in sources:
        for f in files:
            if args.max and indexed >= args.max:
                break
            try:
                d = json.loads(f.read_text(encoding="utf-8"))
            except Exception as exc:  # noqa: BLE001
                print(f"  ✗ {f.name}: {exc}", file=sys.stderr)
                continue
            text = f"{d.get('title', '')}\n\n{d.get('body', '')}"
            doc_id = d.get("synth_id") or f.stem
            payload = {
                "doc_id": doc_id,
                "source_doc": doc_id,
                "title": d.get("title", ""),
                "text": text,
                "grade": d.get("target_grade") or d.get("grade") or "",
                "domain": d.get("domain", ""),
                "llm_provider": d.get("llm_provider", ""),
                "source": src_name,
            }
            batch_ids.append(doc_id)
            batch_texts.append(text)
            batch_payloads.append(payload)

            if len(batch_ids) >= args.batch:
                _flush(store, embedder, args.collection, batch_ids, batch_texts, batch_payloads)
                indexed += len(batch_ids)
                batch_ids, batch_texts, batch_payloads = [], [], []
                if time.time() - last_log > 5:
                    rate = indexed / max(0.001, time.time() - t0)
                    print(f"  [{indexed}/{total_files}] {rate:.1f} docs/s", flush=True)
                    last_log = time.time()
        if args.max and indexed >= args.max:
            break

    if batch_ids:
        _flush(store, embedder, args.collection, batch_ids, batch_texts, batch_payloads)
        indexed += len(batch_ids)

    elapsed = time.time() - t0
    print(f"\n[index] 완료 — {indexed}건 / {elapsed:.1f}s ({indexed/max(0.001, elapsed):.1f} docs/s)")
    print(f"[index] ES collection={args.collection} 인덱싱 완료. /answer 호출 시 citations 채워짐")
    return 0


def _flush(store, embedder, col, ids, texts, payloads):
    result = embedder.embed(texts)
    vectors = getattr(result, "vectors", None) or result
    store.upsert(col, ids, list(vectors), payloads)


if __name__ == "__main__":
    raise SystemExit(main())
