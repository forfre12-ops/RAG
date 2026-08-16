"""PG 검색 지연이 **어디서 오는지** 나눠 잰다.

왜(2026-08-16). 출하 구성(pgvector hybrid)으로 처음 재니 p50=223ms 로 기준(200ms)을
넘었다(`docs/P2_PG_MEASURED_2026-08-16.md`). 그런데 **어디가 느린지 모른다.**
고칠 수 있는 것인지, 서버 사양 문제인지, 설계 한계인지 모르는 상태로는 설명할 수 없다.

hybrid 는 SQL 하나 안에서 셋을 한다(pg_store.search_hybrid).

    d   dense       embedding <=> :q  ORDER BY  LIMIT :cand      pgvector
    l   어휘         tsv @@ to_tsquery + ts_rank ORDER BY LIMIT   GIN
    RRF FULL OUTER JOIN 후 1/(k+rn) 합산

그래서 셋을 따로 돌려 시간을 나눈다. 쿼리 임베딩 시간도 따로 잰다 - 그건 DB 밖이다.

⚠ 이 스크립트는 **적재된 인덱스를 그대로 쓴다.** 다시 임베딩하지 않는다(코퍼스 임베딩이
  16분이라 그것까지 재면 측정이 아니라 재실행이다).

⚠ 측정은 그 서버의 그 PG 구성에 대한 것이다. Postgres 컨테이너 메모리 상한·인덱스
  파라미터가 다르면 값도 다르다. 함께 출력한다.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import time
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

for _s in ("stdout", "stderr"):
    _f = getattr(sys, _s)
    if getattr(_f, "encoding", "") and _f.encoding.lower() not in ("utf-8", "utf-8-sig"):
        import io as _io
        setattr(sys, _s, _io.TextIOWrapper(_f.buffer, encoding="utf-8", errors="replace"))


def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    s = sorted(xs)
    i = min(len(s) - 1, int(round((len(s) - 1) * p)))
    return s[i]


def show(name: str, xs: list[float]) -> dict:
    d = {"n": len(xs), "p50": round(pct(xs, 0.5), 2), "p95": round(pct(xs, 0.95), 2),
         "avg": round(st.mean(xs), 2) if xs else 0.0,
         "min": round(min(xs), 2) if xs else 0.0,
         "max": round(max(xs), 2) if xs else 0.0}
    print(f"  {name:22s} p50 {d['p50']:>8.1f}ms   p95 {d['p95']:>8.1f}ms   "
          f"평균 {d['avg']:>8.1f}ms   ({d['min']:.0f}~{d['max']:.0f})")
    return d


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="PG 검색 지연 분해")
    ap.add_argument("--collection", default="p2_kure_pg_hybrid",
                    help="p2 가 적재한 컬렉션 이름")
    ap.add_argument("--queries-file", default="datasets/gold_real/retrieval_gold.jsonl")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--limit", type=int, default=40, help="쿼리 수(전체면 0)")
    ap.add_argument("--report", default="reports/PG_RETRIEVAL_PROFILE.json")
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "pg")

    from sqlalchemy import text  # noqa: PLC0415

    from koipa.adapters.embedding import build_embedder  # noqa: PLC0415
    from koipa.adapters.vectorstore.pg_store import _CAND_N, PgVectorStore  # noqa: PLC0415

    vs = PgVectorStore()
    emb = build_embedder()
    print(f"임베더: {getattr(emb, 'name', '?')} · dim={getattr(emb, 'dim', '?')}")

    rows = [json.loads(l) for l in Path(args.queries_file).read_text(encoding="utf-8").splitlines()
            if l.strip()]
    if args.limit:
        rows = rows[: args.limit]
    queries = [r.get("query") or r.get("question") or r.get("text") or "" for r in rows]
    queries = [q for q in queries if q.strip()]
    print(f"쿼리 {len(queries)}건 · 컬렉션 {args.collection}\n")

    # --- 환경 먼저 남긴다 - 값이 그 구성에 딸린 것이라 ---------------------------
    env: dict = {}
    with vs._engine.connect() as c:  # noqa: SLF001
        for k, q in (("shared_buffers", "show shared_buffers"),
                     ("work_mem", "show work_mem"),
                     ("effective_cache_size", "show effective_cache_size"),
                     ("max_parallel_workers_per_gather",
                      "show max_parallel_workers_per_gather")):
            try:
                env[k] = c.execute(text(q)).scalar()
            except Exception as exc:  # noqa: BLE001
                env[k] = f"error:{exc}"
        try:
            env["rows"] = c.execute(text(
                "select count(*) from tb_rag_vectors where collection=:c"
            ), {"c": args.collection}).scalar()
        except Exception as exc:  # noqa: BLE001
            env["rows"] = f"error:{exc}"
        try:
            env["indexes"] = [r[0] for r in c.execute(text(
                "select indexdef from pg_indexes where tablename='tb_rag_vectors'"
            )).fetchall()]
        except Exception as exc:  # noqa: BLE001
            env["indexes"] = [f"error:{exc}"]
    print("PG 설정")
    for k, v in env.items():
        if k != "indexes":
            print(f"  {k:32s} {v}")
    for ix in env.get("indexes", []):
        print(f"  index  {str(ix)[:110]}")
    print()

    # --- 0) 워밍업 -------------------------------------------------------------
    # ⚠ 순서가 결과를 바꾼다. 처음에 어휘 -> hybrid 순으로 쟀더니 어휘 1,062ms ·
    #   hybrid 197ms 로 **부분이 전체보다 컸다.** 앞선 패스가 PG 캐시를 덥혀 뒤 패스가
    #   유리해진 것이다. shared_buffers 가 128MB(기본값)인데 코퍼스 벡터만 60MB 를 넘어
    #   캐시 압력이 실재한다.
    #   각 경로를 **두 번 돌려 두 번째만 쓴다** - 셋 다 같은 조건(warm)이 된다.
    def _warm(fn, n: int = 5) -> None:
        for _ in range(n):
            try:
                fn()
            except Exception:  # noqa: BLE001, S110
                pass

    # --- 1) 쿼리 임베딩 (DB 밖) -------------------------------------------------
    t_embed: list[float] = []
    vecs: list[list[float]] = []
    for q in queries:
        t0 = time.perf_counter()
        v = emb.embed([q]).vectors[0]
        t_embed.append((time.perf_counter() - t0) * 1000)
        vecs.append(v)

    # --- 2) dense 만 -----------------------------------------------------------
    _warm(lambda: [vs.search(args.collection, v, top_k=_CAND_N) for v in vecs[:5]], 2)
    t_dense: list[float] = []
    for v in vecs:
        t0 = time.perf_counter()
        # dense 도 hybrid 와 같은 후보 수로 재야 비교가 성립한다.
        vs.search(args.collection, v, top_k=_CAND_N)
        t_dense.append((time.perf_counter() - t0) * 1000)

    # --- 3) 어휘만 (같은 SQL 조각을 직접) ---------------------------------------
    t_lex: list[float] = []
    col = vs._resolve_collection(args.collection)  # noqa: SLF001
    _LEX_SQL = """
                SELECT id FROM tb_rag_vectors
                WHERE collection = :collection AND tsv @@ to_tsquery('simple', :qt)
                ORDER BY ts_rank(tsv, to_tsquery('simple', :qt), 1) DESC
                LIMIT :cand
            """
    with vs._engine.connect() as c:  # noqa: SLF001
        # 워밍업 패스 - 아래 본 측정과 같은 쿼리를 먼저 돌린다
        for q in queries[:10]:
            qw = " | ".join(vs._bigram(q).split()) or "__nomatch__"  # noqa: SLF001
            try:
                c.execute(text(_LEX_SQL), {"collection": col, "qt": qw,
                                           "cand": _CAND_N}).fetchall()
            except Exception:  # noqa: BLE001, S110
                pass
        for q in queries:
            q_or = " | ".join(vs._bigram(q).split()) or "__nomatch__"  # noqa: SLF001
            # ⚠ cand 를 hybrid 와 같은 값으로 둔다. 처음에 200 으로 쟀다가 어휘 단독이
            #   1,057ms 로 나와 hybrid 전체(194ms)보다 커지는 모순이 생겼다 -
            #   부분이 전체보다 클 수 없다. pg_store._CAND_N(=50) 이 실제 값이다.
            t0 = time.perf_counter()
            c.execute(text(_LEX_SQL),
                      {"collection": col, "qt": q_or, "cand": _CAND_N}).fetchall()
            t_lex.append((time.perf_counter() - t0) * 1000)

    # --- 4) hybrid 전체 --------------------------------------------------------
    _warm(lambda: [vs.search_hybrid(args.collection, q, v, top_k=args.top_k)
                   for q, v in list(zip(queries, vecs))[:5]], 2)
    t_hyb: list[float] = []
    for q, v in zip(queries, vecs):
        t0 = time.perf_counter()
        vs.search_hybrid(args.collection, q, v, top_k=args.top_k)
        t_hyb.append((time.perf_counter() - t0) * 1000)

    print("지연 분해")
    out = {
        "embed_query": show("쿼리 임베딩(DB 밖)", t_embed),
        "dense_only": show("dense 만", t_dense),
        "lexical_only": show("어휘 만", t_lex),
        "hybrid_total": show("hybrid 전체", t_hyb),
    }
    rrf = out["hybrid_total"]["p50"] - max(out["dense_only"]["p50"], out["lexical_only"]["p50"])
    print(f"\n  RRF·조인 추정 = hybrid - max(dense, 어휘) = {rrf:.1f}ms")
    if rrf < 0:
        print("  ⚠ 음수다. 부분이 전체보다 크게 측정됐다 - 조건이 다르다는 신호다. "
              "후보 수(cand)·필터가 hybrid 와 같은지 확인할 것.")
    print(f"  P2 게이트가 재는 것: 검색만(임베딩 제외) = hybrid {out['hybrid_total']['p50']:.1f}ms"
          f"   기준 200ms")

    Path(args.report).write_text(
        json.dumps({"env": env, "latency_ms": out, "rrf_estimate_ms": round(rrf, 2),
                    "n_queries": len(queries)}, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    print("\n⚠ 이 값은 **그 서버의 그 PG 구성**에 대한 것이다. 위 설정이 다르면 값도 다르다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
