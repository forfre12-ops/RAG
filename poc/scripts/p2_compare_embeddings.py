"""P2 PoC — 임베딩 모델 + Vector DB 검색 정확도/지연 비교.

두 가지 모드:
  --mode full   : KURE-v1 vs BGE-M3 비교 (HuggingFace 모델 다운로드 필요)
  --mode dryrun : HashEmbedding으로 파이프라인 검증 + 메트릭 포맷 확인

합격선:
  - Recall@5 ≥ 0.80
  - 검색 Latency p50 ≤ 200ms

평가: 합성 코퍼스를 corpus + 등급별 query로 변환.
같은 등급의 문서가 top-K에 들어오면 hit.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_SRC = _HERE.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def load_corpus(synth_dir: Path) -> list[dict]:
    docs: list[dict] = []
    for f in sorted(synth_dir.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        docs.append(
            {
                "id": d["synth_id"],
                "text": f"{d['title']}\n\n{d['body']}",
                "grade": d["target_grade"],
                "domain": d["domain"],
            }
        )
    return docs


def make_queries(per_grade: int = 3) -> list[dict]:
    from lloydk.modules.m3_labeling.seeds import KEYWORD_SEEDS

    grade_kws: dict[str, list[str]] = {"TS": [], "S1": [], "S2": [], "S3": []}
    for s in KEYWORD_SEEDS:
        if s["weight"] >= 0.85:
            grade_kws[s["grade"]].append(s["keyword"])

    queries: list[dict] = []
    for grade in ("TS", "S1", "S2", "S3"):
        for kw in grade_kws[grade][:per_grade]:
            queries.append({"text": f"{kw} 관련 자료", "expected_grade": grade})
    return queries


def evaluate(embedder_name: str, docs: list[dict], queries: list[dict], top_k: int) -> dict:
    from lloydk.adapters.embedding import build_embedder
    from lloydk.adapters.vectorstore import build_store

    emb = build_embedder(embedder_name, force_hash=(embedder_name == "hash"))
    vs = build_store(force_memory=True)

    col = f"p2_{emb.name.replace('/', '_')}"
    vs.ensure_collection(col, dim=emb.dim)

    t0 = time.perf_counter()
    doc_vecs = emb.embed([d["text"] for d in docs]).vectors
    embed_corpus_ms = (time.perf_counter() - t0) * 1000
    vs.upsert(col, [d["id"] for d in docs], doc_vecs, [{"grade": d["grade"]} for d in docs])

    hits = 0
    latencies: list[float] = []
    per_query: list[dict] = []
    for q in queries:
        t1 = time.perf_counter()
        qv = emb.embed([q["text"]]).vectors[0]
        results = vs.search(col, qv, top_k=top_k)
        latencies.append((time.perf_counter() - t1) * 1000)
        hit = any(r.payload.get("grade") == q["expected_grade"] for r in results)
        if hit:
            hits += 1
        per_query.append(
            {
                "query": q["text"],
                "expected_grade": q["expected_grade"],
                "top_grades": [r.payload.get("grade") for r in results],
                "top_scores": [round(r.score, 3) for r in results],
                "hit": hit,
            }
        )

    p95 = (
        statistics.quantiles(latencies, n=20)[18]
        if len(latencies) >= 20
        else max(latencies, default=0)
    )
    return {
        "embedder": emb.name,
        "dim": emb.dim,
        "corpus_size": len(docs),
        "query_count": len(queries),
        "top_k": top_k,
        "recall_at_k": round(hits / len(queries), 4) if queries else 0.0,
        "latency_ms_p50": round(statistics.median(latencies), 2) if latencies else 0.0,
        "latency_ms_p95": round(p95, 2),
        "latency_ms_avg": round(statistics.mean(latencies), 2) if latencies else 0.0,
        "embed_corpus_ms": round(embed_corpus_ms, 1),
        "per_query": per_query,
    }


def write_report(rows: list[dict], out: Path) -> str:
    # dryrun(hash embedding)은 baseline 합격선 0.50, full(KURE/BGE-M3)은 0.80
    is_dryrun = any(r["embedder"] == "hash" for r in rows)
    recall_threshold = 0.50 if is_dryrun else 0.80
    md = [
        "# P2 — 임베딩 · Vector DB 검색 정확도 비교",
        "",
        "_(자리표시자)_",
        "",
        f"_합격선: Recall@K ≥ {recall_threshold:.2f}, Lat p50 ≤ 200ms_  "
        f"{'(dryrun=hash embedding baseline)' if is_dryrun else '(full=KURE/BGE-M3)'}",
        "",
        "| Embedder | Dim | Recall@K | Lat p50 | Lat p95 | Lat avg | Corpus embed | 판정 |",
        "|---|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    overall_pass = True
    for r in rows:
        recall_pass = r["recall_at_k"] >= recall_threshold
        lat_pass = r["latency_ms_p50"] <= 200
        verdict = "PASS" if (recall_pass and lat_pass) else "FAIL"
        if verdict == "FAIL":
            overall_pass = False
        md.append(
            f"| {r['embedder']} | {r['dim']} | {r['recall_at_k']:.3f} | "
            f"{r['latency_ms_p50']:.1f}ms | {r['latency_ms_p95']:.1f}ms | "
            f"{r['latency_ms_avg']:.1f}ms | {r['embed_corpus_ms']:.0f}ms | {verdict} |"
        )

    overall = "PASS" if overall_pass else "FAIL"
    md[2] = f"- **종합 판정**: {overall}"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
    return overall


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["dryrun", "full"], default="dryrun")
    ap.add_argument("--synth-dir", default="datasets/synthetic")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--report", default="reports/p2_embedding_report.md")
    args = ap.parse_args()

    synth_dir = Path(args.synth_dir)
    if not synth_dir.exists() or not list(synth_dir.glob("*.json")):
        print("[p2] no corpus — run p3 first", file=sys.stderr)
        return 2

    docs = load_corpus(synth_dir)
    queries = make_queries(per_grade=3)

    embedders = ["nlpai-lab/KURE-v1", "BAAI/bge-m3"] if args.mode == "full" else ["hash"]

    rows = [evaluate(name, docs, queries, args.top_k) for name in embedders]
    verdict = write_report(rows, Path(args.report))
    print(json.dumps([{k: v for k, v in r.items() if k != "per_query"} for r in rows], ensure_ascii=False, indent=2))
    print(f"\n[p2] verdict: {verdict} -> {args.report}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
