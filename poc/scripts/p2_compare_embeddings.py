"""P2 PoC v2 — 임베딩 모델 + Vector DB 검색 정확도/지연 4-way 비교.

doc/13_벡터DB_ES_전환_계획서.md §9.2 측정 절차.

지원 백엔드 (--backends 옵션, 콤마 구분):
  - inmemory : dense-only, dryrun 기본
  - es       : Elasticsearch (dense kNN / 하이브리드 RRF)

검색 모드 (--mode 옵션):
  - dryrun       : HashEmbedding + InMemoryStore (모델·서버 불필요)
  - full         : KURE-v1 + BGE-M3 실 임베딩 (모델 다운로드 필요)
  - hybrid-only  : full + ES 하이브리드만 측정 (E1 회신 후)

합격선 (doc/13 §9.1, v0.9 유지):
  - Recall@5 ≥ 0.80 (full), 0.50 (dryrun baseline)
  - 검색 Lat p50 ≤ 200ms
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


# ─────────────────────────────────────────────────────────────
# 코퍼스 / 쿼리
# ─────────────────────────────────────────────────────────────


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


# ─────────────────────────────────────────────────────────────
# 평가 — (embedder × backend × search_mode) 조합 1건
# ─────────────────────────────────────────────────────────────


def evaluate(
    embedder_name: str,
    backend: str,
    search_mode: str,  # "dense" | "hybrid"
    docs: list[dict],
    queries: list[dict],
    top_k: int,
) -> dict:
    """단일 (embedder × backend × mode) 조합 평가. 백엔드 미가용 시 SKIP 행 반환."""
    from lloydk.adapters.embedding import build_embedder
    from lloydk.adapters.vectorstore import build_store

    emb = build_embedder(embedder_name, force_hash=(embedder_name == "hash"))

    label = f"{emb.name} / {backend} / {search_mode}"
    try:
        vs = build_store(backend=backend)
        # 실 연결 확인 — ensure_collection이 첫 요청을 발생시킴
        col = f"p2_{emb.name.replace('/', '_').replace('-', '_')}_{backend}"
        vs.ensure_collection(col, dim=emb.dim)
    except Exception as exc:  # noqa: BLE001
        print(f"[p2] SKIP {label}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return {
            "embedder": emb.name,
            "backend": backend,
            "search_mode": search_mode,
            "label": label,
            "dim": emb.dim,
            "status": "SKIP",
            "skip_reason": f"{type(exc).__name__}: {exc}",
            "recall_at_k": 0.0,
            "latency_ms_p50": 0.0,
            "latency_ms_p95": 0.0,
            "latency_ms_avg": 0.0,
            "embed_corpus_ms": 0.0,
            "corpus_size": len(docs),
            "query_count": len(queries),
            "top_k": top_k,
            "per_query": [],
        }

    t0 = time.perf_counter()
    doc_vecs = emb.embed([d["text"] for d in docs]).vectors
    embed_corpus_ms = (time.perf_counter() - t0) * 1000

    # text도 payload에 저장 — 하이브리드에서 BM25가 사용
    payloads = [{"grade": d["grade"], "text": d["text"], "doc_id": d["id"]} for d in docs]
    try:
        vs.upsert(col, [d["id"] for d in docs], doc_vecs, payloads)
    except Exception as exc:  # noqa: BLE001
        print(f"[p2] SKIP {label}: upsert failed — {exc}", file=sys.stderr)
        return {
            "embedder": emb.name, "backend": backend, "search_mode": search_mode,
            "label": label, "dim": emb.dim, "status": "SKIP",
            "skip_reason": f"upsert: {exc}",
            "recall_at_k": 0.0, "latency_ms_p50": 0.0, "latency_ms_p95": 0.0,
            "latency_ms_avg": 0.0, "embed_corpus_ms": round(embed_corpus_ms, 1),
            "corpus_size": len(docs), "query_count": len(queries),
            "top_k": top_k, "per_query": [],
        }

    hits = 0
    latencies: list[float] = []
    per_query: list[dict] = []

    for q in queries:
        t1 = time.perf_counter()
        qv = emb.embed([q["text"]]).vectors[0]

        if search_mode == "hybrid":
            results = vs.search_hybrid(col, q["text"], qv, top_k=top_k)
        else:
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
        "backend": backend,
        "search_mode": search_mode,
        "label": label,
        "status": "OK",
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


# ─────────────────────────────────────────────────────────────
# 리포트
# ─────────────────────────────────────────────────────────────


def write_report(rows: list[dict], out: Path) -> str:
    """4-way 비교 리포트. baseline 행과 △ 컬럼 포함. SKIP 행은 따로 표시."""
    is_dryrun = any(r["embedder"] == "hash" for r in rows)
    recall_threshold = 0.50 if is_dryrun else 0.80

    ok_rows = [r for r in rows if r.get("status") == "OK"]
    skipped = [r for r in rows if r.get("status") == "SKIP"]

    if not ok_rows:
        # 모두 SKIP — 리포트만 남기고 FAIL
        md = [
            "# P2 v2 — 임베딩 · Vector DB 4-way 비교",
            "",
            "- **종합 판정**: FAIL (모든 백엔드 SKIP)",
            "",
        ]
        for r in skipped:
            md.append(f"- SKIP: `{r['label']}` — {r['skip_reason']}")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("\n".join(md), encoding="utf-8")
        out.with_suffix(".json").write_text(
            json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        return "FAIL"

    baseline = ok_rows[0]
    base_recall = baseline["recall_at_k"]

    md = [
        "# P2 v2 — 임베딩 · Vector DB 4-way 비교",
        "",
        "_(자리표시자)_",
        "",
        f"_합격선: Recall@K ≥ {recall_threshold:.2f}, Lat p50 ≤ 200ms_  "
        f"{'(dryrun=hash embedding baseline)' if is_dryrun else '(full=KURE/BGE-M3)'}",
        "",
        f"_baseline: **{baseline['label']}**, △Recall은 baseline 대비_",
        "",
        "| 조합 | Dim | Recall@K | △Recall | Lat p50 | Lat p95 | Lat avg | 판정 |",
        "|---|---:|---:|---:|---:|---:|---:|:---:|",
    ]
    overall_pass = True
    for r in ok_rows:
        recall_pass = r["recall_at_k"] >= recall_threshold
        lat_pass = r["latency_ms_p50"] <= 200
        verdict = "PASS" if (recall_pass and lat_pass) else "FAIL"
        if verdict == "FAIL":
            overall_pass = False
        delta = r["recall_at_k"] - base_recall
        delta_str = f"{delta:+.3f}" if r is not baseline else "—"
        md.append(
            f"| {r['label']} | {r['dim']} | {r['recall_at_k']:.3f} | {delta_str} | "
            f"{r['latency_ms_p50']:.1f}ms | {r['latency_ms_p95']:.1f}ms | "
            f"{r['latency_ms_avg']:.1f}ms | {verdict} |"
        )

    if skipped:
        md += ["", "## SKIP된 백엔드", ""]
        for r in skipped:
            md.append(f"- `{r['label']}` — {r['skip_reason']}")

    # 합격선 상향 협의 메모 (doc/13 §9.2)
    md += [
        "",
        "## 합격선 상향 판정 (doc/13 §9.2)",
        "",
        f"- 현행 합격선: Recall@5 ≥ {recall_threshold:.2f}, Lat p50 ≤ 200ms (유지)",
        "- 상향 협의 조건: 하이브리드 RRF 행이 baseline 대비 △Recall ≥ +0.05 + Lat p95 +50ms 이내",
        "- 본 측정 결과를 발주처와 협의하여 v1.1에서 합격선 갱신 여부 확정",
    ]

    overall = "PASS" if overall_pass else "FAIL"
    md[2] = f"- **종합 판정**: {overall}"

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8")
    out.with_suffix(".json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return overall


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────


def _resolve_combinations(
    mode: str,
    backends: list[str],
    hybrid: bool,
) -> list[tuple[str, str, str]]:
    """(embedder_name, backend, search_mode) 조합 목록 산출."""
    if mode == "dryrun":
        embedders = ["hash"]
    elif mode == "full":
        embedders = ["nlpai-lab/KURE-v1", "BAAI/bge-m3"]
    elif mode == "hybrid-only":
        embedders = ["nlpai-lab/KURE-v1"]
    else:
        raise ValueError(f"unknown mode: {mode}")

    combos: list[tuple[str, str, str]] = []
    for emb in embedders:
        for backend in backends:
            combos.append((emb, backend, "dense"))
            # hybrid는 ES에서만 의미있음 — 다른 백엔드는 vec-only 폴리필이라 dense와 동일
            # doc/13 §5.2·§9.1 한계 명시
            if hybrid and backend == "es":
                combos.append((emb, backend, "hybrid"))
    return combos


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["dryrun", "full", "hybrid-only"], default="dryrun")
    ap.add_argument(
        "--backends",
        default="inmemory",
        help="콤마 구분 백엔드 목록 (es,inmemory). dryrun은 기본 inmemory.",
    )
    ap.add_argument(
        "--hybrid",
        action="store_true",
        help="ES 백엔드에서 dense·hybrid 두 모드 모두 측정",
    )
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

    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    combos = _resolve_combinations(args.mode, backends, args.hybrid)

    print(f"[p2] mode={args.mode}, combos={len(combos)}, backends={backends}, hybrid={args.hybrid}")

    rows = [
        evaluate(emb, backend, search_mode, docs, queries, args.top_k)
        for emb, backend, search_mode in combos
    ]
    verdict = write_report(rows, Path(args.report))
    summary = [{k: v for k, v in r.items() if k != "per_query"} for r in rows]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n[p2] verdict: {verdict} -> {args.report}")
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
