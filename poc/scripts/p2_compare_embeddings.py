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
# scripts/도 sys.path에 — p2_intent_queries 같은 동급 모듈 import 위해
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from lloydk.config import settings


# ─────────────────────────────────────────────────────────────
# 코퍼스 / 쿼리
# ─────────────────────────────────────────────────────────────


def load_corpus(
    synth_dir: Path,
    max_chars: int = 1500,
    chunk_size: int = 0,
    chunk_overlap: int = 100,
) -> list[dict]:
    """문서 코퍼스 로드.

    chunk_size > 0: 각 문서를 chunk_size 글자 단위로 분할 + overlap 적용.
    청킹 시 hit 판정은 base_doc_id 기준으로 집계.
    """
    docs: list[dict] = []
    for f in sorted(synth_dir.glob("*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        base_id = d.get("synth_id") or d.get("source_file") or f.stem
        title = d.get("title", "")
        body = d.get("body", "")
        full_text = f"{title}\n\n{body}"

        if chunk_size > 0:
            # 슬라이딩 윈도우 청킹
            step = max(1, chunk_size - chunk_overlap)
            chunks = []
            start = 0
            while start < len(full_text):
                end = start + chunk_size
                chunks.append(full_text[start:end])
                if end >= len(full_text):
                    break
                start += step
            for i, chunk in enumerate(chunks):
                docs.append({
                    "id": f"{base_id}-c{i}",
                    "base_doc_id": base_id,  # hit 판정용 원본 ID
                    "text": chunk,
                    "grade": d["target_grade"],
                    "domain": d["domain"],
                })
        else:
            docs.append({
                "id": base_id,
                "base_doc_id": base_id,
                "text": full_text[:max_chars],
                "grade": d["target_grade"],
                "domain": d["domain"],
            })
    return docs


def make_queries(
    per_grade: int = 3,
    style: str = "seed",
    synth_dir: Path | None = None,
) -> list[dict]:
    """쿼리 생성.

    style:
      "seed"        : {kw} 관련 자료 패턴 (기존 기본값).
      "intent"      : 의도형 30종 고정 (p2_intent_queries.py).
      "doc_derived" : 합성 문서 title 파생 쿼리. hit=doc_id 기준 (더 엄밀).
                      synth_dir 필수. 등급당 per_grade개 샘플링.
    """
    if style == "intent":
        from p2_intent_queries import INTENT_QUERIES
        return list(INTENT_QUERIES)

    if style == "doc_derived":
        if synth_dir is None:
            raise ValueError("doc_derived 스타일은 synth_dir 필수")
        from p2_doc_derived_queries import build_doc_derived_queries
        return build_doc_derived_queries(synth_dir, n_per_grade=per_grade)

    if style == "koipa":
        # OSS/KOIPA 코퍼스용 — 문서 본문 문장 추출 쿼리 (doc_id 기준 hit)
        # synth_dir 옆에 *_eval_queries.json 파일 자동 탐색
        candidates = [
            Path("datasets/oss_eval_queries.json"),
            Path("datasets/koipa_eval_queries.json"),
        ]
        if synth_dir is not None:
            parent = synth_dir.parent
            candidates += list(parent.glob("*_eval_queries.json"))
        for qp in candidates:
            if qp.exists():
                return json.loads(qp.read_text(encoding="utf-8"))
        raise FileNotFoundError(
            "koipa/oss 쿼리 파일 없음. "
            "python scripts/p2_oss_corpus_builder.py 먼저 실행"
        )

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
    reranker_provider: str | None = None,
    oversample_factor: int = 4,
) -> dict:
    """단일 (embedder × backend × mode) 조합 평가. 백엔드 미가용 시 SKIP 행 반환.

    reranker_provider: None이면 reranker 미적용. "noop"/"bge"/"qwen3" 지정 시
        1차 dense에서 top_k*oversample_factor만큼 가져온 뒤 cross-encoder로 재정렬.
    """
    from lloydk.adapters.embedding import build_embedder, embedder_digest
    from lloydk.adapters.vectorstore import build_store

    emb = build_embedder(embedder_name, force_hash=(embedder_name == "hash"))

    # CachedEmbedding wrap 시 emb.name="cached"라 모델 구분이 사라짐.
    # underlying 모델명을 우선 노출 — 안 보이면 emb.name 폴백.
    display_name = getattr(emb, "_underlying_name", None) or emb.name
    rr_label = f" + rr={reranker_provider}" if reranker_provider else ""
    label = f"{display_name} / {backend} / {search_mode}{rr_label}"

    # [embedder digest] 실 모델을 요청했는데 HashEmbedding 이 돌아왔는지를 **결과에 박는다.**
    # 실서빙은 require_real_embedder 가 기동을 거부하지만(api/app.py) 이 평가 스크립트 경로에는
    # 무음 폴백이 남아 있다. 그 자체를 막을 이유는 없다(오프라인 드라이런은 정당한 작업) —
    # 문제는 그렇게 나온 recall 수치가 나중에 실 임베더 수치와 구분되지 않는 것이다.
    digest = embedder_digest(emb, requested=embedder_name)
    if digest["degraded"]:
        print(
            f"[p2] WARNING {label}: 실 임베더 {embedder_name!r} 를 요청했으나 hash 로 열화됐다 — "
            "이 행의 recall 은 검색품질 근거로 인용하지 말 것(embedder_digest.degraded=true).",
            file=sys.stderr,
        )

    # reranker lazy 빌드 — 첫 쿼리에서 모델 다운로드/로드 비용 발생
    reranker = None
    if reranker_provider:
        from lloydk.adapters.reranker import get_reranker
        reranker = get_reranker(reranker_provider)
    try:
        vs = build_store(backend=backend)
        # 실 연결 확인 — ensure_collection이 첫 요청을 발생시킴
        col = f"p2_{emb.name.replace('/', '_').replace('-', '_')}_{backend}"
        vs.ensure_collection(col, dim=emb.dim)
    except Exception as exc:  # noqa: BLE001
        print(f"[p2] SKIP {label}: {type(exc).__name__}: {exc}", file=sys.stderr)
        return {
            "embedder": display_name,
            "backend": backend,
            "search_mode": search_mode,
            "label": label,
            "dim": emb.dim,
            "embedder_digest": digest,
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

    # 100건씩 배치 임베딩 → 즉시 ES 적재 (진행 상황 실시간 확인 가능)
    BATCH = 100
    t0 = time.perf_counter()
    total = len(docs)
    for batch_start in range(0, total, BATCH):
        batch = docs[batch_start: batch_start + BATCH]
        batch_vecs = emb.embed([d["text"] for d in batch]).vectors
        payloads_batch = [
            {
                "grade": d["grade"],
                "text": d["text"],
                "doc_id": d["id"],
                "base_doc_id": d.get("base_doc_id", d["id"]),
            }
            for d in batch
        ]
        try:
            vs.upsert(col, [d["id"] for d in batch], batch_vecs, payloads_batch)
        except Exception as exc:  # noqa: BLE001
            print(f"[p2] SKIP {label}: upsert failed — {exc}", file=sys.stderr)
            return {
                "embedder": display_name, "backend": backend, "search_mode": search_mode,
                "label": label, "dim": emb.dim, "embedder_digest": digest, "status": "SKIP",
                "skip_reason": f"upsert: {exc}",
                "recall_at_k": 0.0, "latency_ms_p50": 0.0, "latency_ms_p95": 0.0,
                "latency_ms_avg": 0.0, "embed_corpus_ms": 0.0,
                "corpus_size": len(docs), "query_count": len(queries),
                "top_k": top_k, "per_query": [],
            }
        done = min(batch_start + BATCH, total)
        print(f"[p2] {label} 적재: {done}/{total}", flush=True)
    embed_corpus_ms = (time.perf_counter() - t0) * 1000

    hits = 0
    latencies: list[float] = []
    per_query: list[dict] = []

    # reranker 사용 시 1차 dense는 oversample (top_k * factor), reranker로 top_k까지 좁힘
    first_k = top_k * max(1, oversample_factor) if reranker else top_k

    # 워밍업 1회 — 첫 쿼리는 임베딩 모델 cold start로 lat outlier가 됨(KURE 1차 측정에서
    # p95=4535ms, avg=1482ms 관측). 첫 쿼리를 한 번 미리 돌려 모델·CUDA 컨텍스트를 데움.
    # 측정 통계에는 미포함.
    if queries:
        try:
            wq = queries[0]["text"]
            wqv = emb.embed([wq]).vectors[0]
            warm_results = (
                vs.search_hybrid(col, wq, wqv, top_k=first_k)
                if search_mode == "hybrid"
                else vs.search(col, wqv, top_k=first_k)
            )
            if reranker and warm_results:
                warm_cands = [r.payload.get("text", "") for r in warm_results]
                reranker.rerank(wq, warm_cands, top_k=top_k)
        except Exception as exc:  # noqa: BLE001
            print(f"[p2] warmup skipped: {exc}", file=sys.stderr)

    for q in queries:
        t1 = time.perf_counter()
        qv = emb.embed([q["text"]]).vectors[0]

        if search_mode == "hybrid":
            results = vs.search_hybrid(col, q["text"], qv, top_k=first_k)
        else:
            results = vs.search(col, qv, top_k=first_k)

        # reranker 적용 — payload의 text를 cross-encoder에 넘김
        if reranker and results:
            try:
                cands = [r.payload.get("text", "") for r in results]
                ranked = reranker.rerank(q["text"], cands, top_k=top_k)
                # ranked는 RerankResult(index, score, document) 정렬됨 — 원본 results를 그 순서로 슬라이스
                results = [results[r.index] for r in ranked]
            except Exception as exc:  # noqa: BLE001
                # reranker 실패 시 dense 순서로 폴백 (top_k 슬라이스)
                print(f"[p2] reranker failed for q={q['text']!r}: {exc}", file=sys.stderr)
                results = results[:top_k]
        elif reranker and not results:
            results = []
        else:
            # reranker 미사용 경로는 first_k == top_k이므로 그대로
            pass

        latencies.append((time.perf_counter() - t1) * 1000)
        expected_doc_id = q.get("expected_doc_id")
        relevant_doc_ids = q.get("relevant_doc_ids") or ([expected_doc_id] if expected_doc_id else [])
        relevant_doc_ids = [str(x) for x in relevant_doc_ids if x]
        if relevant_doc_ids:
            # doc_id 또는 base_doc_id (청킹 시) 기준 hit
            # 청킹된 경우 chunk ID = "{base_id}-c{i}" 형태
            relevant_set = set(relevant_doc_ids)
            hit = False
            for r in results:
                doc_id = str(r.payload.get("doc_id", ""))
                base_doc_id = str(r.payload.get("base_doc_id", ""))
                if (
                    doc_id in relevant_set
                    or base_doc_id in relevant_set
                    or any(doc_id.startswith(rel + "-c") for rel in relevant_set)
                    or any(doc_id.startswith(rel + ":c") for rel in relevant_set)
                ):
                    hit = True
                    break
        else:
            # seed/intent 스타일: 올바른 등급 문서가 top-K에 있으면 hit
            hit = any(r.payload.get("grade") == q["expected_grade"] for r in results)
        if hit:
            hits += 1
        per_query.append(
            {
                "query": q["text"],
                "expected_grade": q["expected_grade"],
                "expected_doc_id": expected_doc_id,
                "relevant_doc_ids": relevant_doc_ids,
                "top_grades": [r.payload.get("grade") for r in results],
                "top_doc_ids": [r.payload.get("doc_id") for r in results],
                "top_base_doc_ids": [r.payload.get("base_doc_id") for r in results],
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
        "embedder": display_name,
        "backend": backend,
        "search_mode": search_mode,
        "reranker": reranker_provider,
        "oversample_factor": oversample_factor if reranker_provider else None,
        "label": label,
        "status": "OK",
        "dim": emb.dim,
        "embedder_digest": digest,
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


def write_report(rows: list[dict], out: Path, *, best_config_mode: bool = True) -> str:
    """4-way 비교 리포트. baseline 행과 △ 컬럼 포함. SKIP 행은 따로 표시."""
    from lloydk.modules.m6_evaluation.retrieval_metrics import (
        compute_retrieval_metrics_from_arrays,
    )

    for r in rows:
        per_query = r.get("per_query") or []
        preds: list[list[str]] = []
        rels: list[list[str]] = []
        qids: list[str] = []
        for i, pq in enumerate(per_query):
            relevant = pq.get("relevant_doc_ids") or (
                [pq["expected_doc_id"]] if pq.get("expected_doc_id") else []
            )
            if not relevant:
                continue
            preds.append(pq.get("top_base_doc_ids") or pq.get("top_doc_ids") or [])
            rels.append(relevant)
            qids.append(pq.get("query") or str(i))
        if preds:
            metrics = compute_retrieval_metrics_from_arrays(
                preds, rels, top_k=int(r.get("top_k", 5)), query_ids=qids,
            )
            r["retrieval_metrics"] = {
                k: round(v, 4) if isinstance(v, float) else v
                for k, v in metrics.to_dict().items()
                if k != "per_query"
            }

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
    best = max(ok_rows, key=lambda r: (r["recall_at_k"], -r["latency_ms_p50"]))
    best_pass = best["recall_at_k"] >= recall_threshold and best["latency_ms_p50"] <= 200

    md = [
        "# P2 v2 — 임베딩 · Vector DB 4-way 비교",
        "",
        "_(자리표시자)_",
        "",
        f"_합격선: Recall@K ≥ {recall_threshold:.2f}, Lat p50 ≤ 200ms_  "
        f"{'(dryrun=hash embedding baseline)' if is_dryrun else '(full=KURE/BGE-M3)'}",
        "",
        f"_baseline: **{baseline['label']}**, △Recall은 baseline 대비_",
        f"_operational candidate: **{best['label']}** "
        f"(Recall@K={best['recall_at_k']:.3f}, p50={best['latency_ms_p50']:.1f}ms)_",
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

    metric_rows = [r for r in ok_rows if r.get("retrieval_metrics")]
    if metric_rows:
        md += [
            "",
            "## Retrieval Gold Metrics",
            "",
            "| Config | N | Recall@K | MRR | nDCG@K | Precision@K |",
            "|---|---:|---:|---:|---:|---:|",
        ]
        for r in metric_rows:
            m = r["retrieval_metrics"]
            md.append(
                f"| {r['label']} | {m['sample_count']} | {m['recall_at_k']:.3f} | "
                f"{m['mrr']:.3f} | {m['ndcg_at_k']:.3f} | {m['precision_at_k']:.3f} |"
            )

    overall = "PASS" if (best_pass if best_config_mode else overall_pass) else "FAIL"
    md[2] = f"- **종합 판정**: {overall}"
    if best_config_mode:
        md.insert(3, f"- **판정 기준**: best operational config 기준 ({best['label']})")

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(md), encoding="utf-8")
    payload = {
        "verdict": overall,
        "best_config_mode": best_config_mode,
        "best_config": {k: v for k, v in best.items() if k != "per_query"},
        "rows": rows,
    }
    out.with_suffix(".json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return overall


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────


def _resolve_combinations(
    mode: str,
    backends: list[str],
    hybrid: bool,
    models_override: list[str] | None = None,
) -> list[tuple[str, str, str]]:
    """(embedder_name, backend, search_mode) 조합 목록 산출.

    models_override가 주어지면 mode와 무관하게 해당 목록을 사용한다.
    Phase 2 (5070 Ti 풀가동) — KURE/BGE-M3/dragonkue 3-way 측정을 위해 도입.
    """
    if models_override:
        embedders = list(models_override)
    elif mode == "dryrun":
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
    ap.add_argument("--chunk-size", type=int, default=settings.rag_index_chunk_size,
                    help="청크 크기(글자). 0=청킹 없음. 기본 1200은 운영 후보(KURE+ES hybrid) 기준.")
    ap.add_argument("--chunk-overlap", type=int, default=settings.rag_index_chunk_overlap,
                    help="청크 간 중복 글자 수 (기본 100)")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument(
        "--queries-per-grade",
        type=int,
        default=3,
        help="등급당 쿼리 개수. dryrun=3 권장, full=9+ (총 36+) 권장. intent 스타일은 무시됨.",
    )
    ap.add_argument(
        "--query-style",
        default="seed",
        choices=["seed", "intent", "doc_derived", "koipa"],
        help=(
            "쿼리 스타일. "
            "seed: `{kw} 관련 자료` 시드 키워드 기반(기본). "
            "intent: 의도형 30종 고정 (scripts/p2_intent_queries.py). "
            "doc_derived: 합성 문서 title 파생, hit=doc_id 기준 (더 엄밀한 평가). "
            "koipa: KOIPA 기출문제 본문 문장 쿼리, hit=doc_id 기준 (가장 현실적)."
        ),
    )
    ap.add_argument(
        "--reranker",
        default=None,
        choices=["noop", "bge", "qwen3"],
        help="reranker 적용. 지정 시 1차 dense에서 oversample 후 cross-encoder 재정렬. 미지정 시 미적용.",
    )
    ap.add_argument(
        "--oversample-factor",
        type=int,
        default=4,
        help="reranker 사용 시 1차 dense에서 top_k × factor만큼 가져옴. 기본 4.",
    )
    ap.add_argument(
        "--queries-file",
        default=None,
        help=(
            "외부 쿼리 JSONL/JSON 파일 경로. 지정 시 --query-style 무시.\n"
            "각 레코드: {text|query, expected_doc_id(권장), expected_grade(없으면 grade hit 불가)}\n"
            "expected_doc_id 있으면 doc_id 기준 Recall — 진짜 RAG 지표.\n"
            "예: datasets/gold_real/retrieval_gold.jsonl, datasets/oss_eval_queries.json"
        ),
    )
    ap.add_argument("--report", default="reports/p2_embedding_report.md")
    ap.add_argument(
        "--strict-all",
        action="store_true",
        help="모든 측정 조합이 기준을 통과해야 PASS. 기본은 best operational config 기준.",
    )
    ap.add_argument(
        "--report-only",
        action="store_true",
        help="Always exit 0 after writing the report. Useful for dryrun gold metric snapshots.",
    )
    ap.add_argument(
        "--models",
        default=None,
        help=(
            "콤마 구분 임베딩 모델 ID 목록. 지정 시 --mode와 무관하게 해당 모델만 측정. "
            "Phase 2 (5070 Ti 풀가동) 3-way 측정용. "
            "예: nlpai-lab/KURE-v1,BAAI/bge-m3,dragonkue/BGE-m3-ko"
        ),
    )
    args = ap.parse_args()

    synth_dir = Path(args.synth_dir)
    if not synth_dir.exists() or not list(synth_dir.glob("*.json")):
        print("[p2] no corpus — run p3 first", file=sys.stderr)
        return 2

    docs = load_corpus(synth_dir,
                       chunk_size=getattr(args, "chunk_size", 0),
                       chunk_overlap=getattr(args, "chunk_overlap", 100))

    if args.queries_file:
        qfile = Path(args.queries_file)
        if not qfile.exists():
            print(f"[p2] --queries-file 없음: {qfile}", file=sys.stderr)
            return 2
        qtext = qfile.read_text(encoding="utf-8")
        if qfile.suffix.lower() == ".jsonl":
            raw_queries = [json.loads(line) for line in qtext.splitlines() if line.strip()]
        else:
            raw = json.loads(qtext)
            if isinstance(raw, list):
                raw_queries = raw
            else:
                raw_queries = list(raw.values()) if isinstance(raw, dict) else []
        # 필드 정규화: query/text 둘 다 지원
        queries = []
        for q in raw_queries:
            relevant_doc_ids = q.get("relevant_doc_ids") or []
            expected_doc_id = q.get("expected_doc_id") or (relevant_doc_ids[0] if relevant_doc_ids else None)
            entry = {
                "text": q.get("query") or q.get("text", ""),
                "expected_grade": q.get("expected_grade", ""),
                "expected_doc_id": expected_doc_id,
                "relevant_doc_ids": relevant_doc_ids or ([expected_doc_id] if expected_doc_id else []),
            }
            if entry["text"]:
                queries.append(entry)
        has_doc_ids = sum(1 for q in queries if q.get("expected_doc_id"))
        print(
            f"[p2] queries-file={args.queries_file}, count={len(queries)}, "
            f"doc_id_based={has_doc_ids}/{len(queries)}"
        )
    else:
        queries = make_queries(
            per_grade=args.queries_per_grade,
            style=args.query_style,
            synth_dir=synth_dir if args.query_style == "doc_derived" else None,
        )
        print(f"[p2] query_style={args.query_style}, query_count={len(queries)}")

    backends = [b.strip() for b in args.backends.split(",") if b.strip()]
    models_override = None
    if args.models:
        models_override = [m.strip() for m in args.models.split(",") if m.strip()]
    combos = _resolve_combinations(args.mode, backends, args.hybrid, models_override)

    print(f"[p2] mode={args.mode}, combos={len(combos)}, backends={backends}, hybrid={args.hybrid}")
    if models_override:
        print(f"[p2] models override: {models_override}")

    rows = [
        evaluate(
            emb, backend, search_mode, docs, queries, args.top_k,
            reranker_provider=args.reranker,
            oversample_factor=args.oversample_factor,
        )
        for emb, backend, search_mode in combos
    ]
    verdict = write_report(rows, Path(args.report), best_config_mode=not args.strict_all)
    summary = [{k: v for k, v in r.items() if k != "per_query"} for r in rows]
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    print(f"\n[p2] verdict: {verdict} -> {args.report}")
    if args.report_only:
        return 0
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
