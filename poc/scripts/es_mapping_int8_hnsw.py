"""P2-D2: ES int8_hnsw 양자화 매핑 신설 + recall 회귀 측정 도우미.

목표:
- dense_vector를 int8 양자화하여 메모리 50% 감소
- HNSW 인덱스 유지
- recall 회귀가 ≥1%p 이내인지 확인

사용:
  python scripts/es_mapping_int8_hnsw.py --create lloydk-docs-v2
  python scripts/es_mapping_int8_hnsw.py --recall-check \
      --baseline lloydk-docs \
      --target lloydk-docs-v2 \
      --queries datasets/eval/queries_v1.jsonl
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


MAPPING_INT8_HNSW = {
    "settings": {
        "analysis": {
            "analyzer": {
                "korean_nori": {
                    "type": "custom",
                    "tokenizer": "nori_tokenizer",
                    "filter": ["nori_part_of_speech", "lowercase"],
                },
            },
        },
        "index.knn": True,
    },
    "mappings": {
        "properties": {
            "doc_id": {"type": "keyword"},
            "tenant_id": {"type": "keyword"},
            "title": {"type": "text", "analyzer": "korean_nori"},
            "body": {"type": "text", "analyzer": "korean_nori"},
            "grade": {"type": "keyword"},
            "embedding": {
                "type": "dense_vector",
                "dims": 1024,
                "index": True,
                "similarity": "cosine",
                "index_options": {
                    "type": "int8_hnsw",   # 핵심 변경 — 메모리 50% 감소
                    "m": 16,
                    "ef_construction": 100,
                },
            },
            "created_at": {"type": "date"},
        }
    },
}


def create_index(es_url: str, index_name: str) -> None:
    import httpx  # type: ignore
    url = f"{es_url}/{index_name}"
    r = httpx.put(url, json=MAPPING_INT8_HNSW, timeout=30)
    r.raise_for_status()
    print(f"[OK] index created: {index_name}")
    print(r.json())


def recall_check(es_url: str, baseline: str, target: str, queries_path: Path, top_k: int = 5) -> dict:
    import httpx  # type: ignore

    rows = []
    with queries_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))

    def _search(index: str, q_text: str) -> list[str]:
        body = {
            "size": top_k,
            "query": {"multi_match": {"query": q_text, "fields": ["title", "body"]}},
        }
        r = httpx.post(f"{es_url}/{index}/_search", json=body, timeout=15)
        r.raise_for_status()
        hits = r.json().get("hits", {}).get("hits", [])
        return [h["_id"] for h in hits]

    base_hits = 0
    tgt_hits = 0
    overlap = 0
    for row in rows:
        q = row["query"]
        rel = set(row.get("relevant_ids", []))
        bh = set(_search(baseline, q))
        th = set(_search(target, q))
        base_hits += len(bh & rel)
        tgt_hits += len(th & rel)
        overlap += len(bh & th)

    total_rel = sum(len(r.get("relevant_ids", [])) for r in rows)
    report = {
        "baseline_recall": round(base_hits / max(total_rel, 1), 4),
        "target_recall": round(tgt_hits / max(total_rel, 1), 4),
        "overlap_ratio": round(overlap / max(top_k * len(rows), 1), 4),
        "n_queries": len(rows),
        "top_k": top_k,
    }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="D2: int8_hnsw 매핑·회귀")
    parser.add_argument("--es-url", default="http://localhost:9200")
    parser.add_argument("--create", metavar="INDEX_NAME")
    parser.add_argument("--recall-check", action="store_true")
    parser.add_argument("--baseline", default="lloydk-docs")
    parser.add_argument("--target", default="lloydk-docs-v2")
    parser.add_argument("--queries", type=Path)
    args = parser.parse_args()

    if args.create:
        create_index(args.es_url, args.create)
        return 0

    if args.recall_check:
        if not args.queries or not args.queries.exists():
            print("[ERR] --queries jsonl 필요")
            return 2
        report = recall_check(args.es_url, args.baseline, args.target, args.queries)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        # 회귀 임계: baseline 대비 -1%p 이상 시 fail
        if report["baseline_recall"] - report["target_recall"] > 0.01:
            print("[FAIL] target recall regressed ≥1%p")
            return 2
        return 0

    print("[USAGE] --create INDEX | --recall-check ...")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
