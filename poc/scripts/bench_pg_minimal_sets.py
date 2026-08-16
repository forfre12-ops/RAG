"""병렬을 켜는 데 **실제로 필요한 설정만** 남긴다.

왜(2026-08-16). `bench_pg_concurrency.py` 스윕에서 강제4 가 전 구간 균형점으로 나왔다.

    동시1  189.5/221.2 -> 125.4/143.8
    동시2  214.2/261.1 -> 148.7/207.5
    동시4  260.5/342.6 -> 184.7/555.0   (p50 이득·p95 손해)
    동시8  포화 - 설정과 무관하게 처리량 13~15건/s 에서 막힌다

그때 네 가지를 한꺼번에 걸었다.

    parallel_setup_cost = 0
    parallel_tuple_cost = 0
    min_parallel_table_scan_size = 0
    max_parallel_workers_per_gather = 4

운영 설정을 바꿀 때는 **바꾸는 범위가 작을수록 좋다.** 안 걸어도 되는 것을 걸면
다른 쿼리까지 영향을 받는다. 그래서 하나씩 빼면서 효과가 유지되는지 본다.

특히 `min_parallel_table_scan_size` 는 기본 8MB 인데 이 테이블 힙이 26MB 라
**이미 조건을 넘는다** - 안 걸어도 될 가능성이 크다. 확인 대상이다.
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

from koipa_sql_variants import SQL_NOW  # noqa: E402

VARIANTS: list[tuple[str, tuple[str, ...]]] = [
    ("기본", ()),
    ("넷 다", ("SET parallel_setup_cost = 0",
               "SET parallel_tuple_cost = 0",
               "SET min_parallel_table_scan_size = 0",
               "SET max_parallel_workers_per_gather = 4")),
    ("scan_size 뺌", ("SET parallel_setup_cost = 0",
                      "SET parallel_tuple_cost = 0",
                      "SET max_parallel_workers_per_gather = 4")),
    ("tuple_cost 도 뺌", ("SET parallel_setup_cost = 0",
                          "SET max_parallel_workers_per_gather = 4")),
    ("워커수만", ("SET max_parallel_workers_per_gather = 4",)),
    ("setup_cost 만", ("SET parallel_setup_cost = 0",)),
]


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(round((len(s) - 1) * p)))] if s else 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="병렬에 필요한 최소 설정")
    ap.add_argument("--collection", default="p2_cached_pg")
    ap.add_argument("--queries-file", default="datasets/gold_real/retrieval_gold.jsonl")
    ap.add_argument("--limit", type=int, default=40)
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--report", default="reports/PG_MINIMAL_SETS.json")
    args = ap.parse_args(argv)

    os.environ.setdefault("TESTING", "1")
    os.environ.setdefault("VECTOR_BACKEND", "pg")

    from sqlalchemy import text  # noqa: PLC0415

    from koipa.adapters.embedding import build_embedder  # noqa: PLC0415
    from koipa.adapters.vectorstore.pg_store import _CAND_N, _RRF_K, PgVectorStore  # noqa: PLC0415

    vs = PgVectorStore()
    emb = build_embedder()
    col = vs._resolve_collection(args.collection)  # noqa: SLF001

    rows = [json.loads(line) for line in
            Path(args.queries_file).read_text(encoding="utf-8").splitlines() if line.strip()]
    qs = [r.get("query") or r.get("question") or r.get("text") or "" for r in rows]
    qs = [q for q in qs if q.strip()][: args.limit]
    prep = [{"collection": col,
             "qt": " | ".join(vs._bigram(q).split()) or "__nomatch__",  # noqa: SLF001
             "q": vs._vec_lit(emb.embed([q]).vectors[0]),  # noqa: SLF001
             "cand": _CAND_N, "k": _RRF_K, "topk": args.top_k} for q in qs]
    print(f"쿼리 {len(prep)}건 · 컬렉션 {args.collection}\n")

    def run(sets: tuple[str, ...], p: dict) -> tuple[float, list]:
        with vs._engine.connect() as c:
            for s in sets:
                c.execute(text(s))
            t0 = time.perf_counter()
            got = c.execute(text(SQL_NOW), p).fetchall()
            ms = (time.perf_counter() - t0) * 1000
        return ms, [(r[0], round(float(r[3]), 12)) for r in got]

    base_out = {i: run((), p)[1] for i, p in enumerate(prep)}

    print(f"  {'설정':<18s} {'p50':>9s} {'p95':>9s} {'워커':>4s}  {'동일':>5s}")
    out: dict = {"collection": args.collection, "n": len(prep), "runs": []}
    for name, sets in VARIANTS:
        for p in prep[:5]:
            run(sets, p)
        ts: list[float] = []
        diff = 0
        for i, p in enumerate(prep):
            ms, got = run(sets, p)
            ts.append(ms)
            if got != base_out[i]:
                diff += 1
        with vs._engine.connect() as c:
            for s in sets:
                c.execute(text(s))
            pj = c.execute(text(f"EXPLAIN (ANALYZE, FORMAT JSON){SQL_NOW}"), prep[0]).scalar()
            pj = pj[0] if isinstance(pj, list) else json.loads(pj)[0]

            def _w(n: dict) -> int:
                m = n.get("Workers Launched", 0) or 0
                for ch in n.get("Plans", []) or []:
                    m = max(m, _w(ch))
                return m
            workers = _w(pj["Plan"])
        print(f"  {name:<18s} {pct(ts, 0.5):>8.1f}ms {pct(ts, 0.95):>8.1f}ms {workers:>4d}  "
              f"{len(prep) - diff:>2d}/{len(prep)}")
        out["runs"].append({"name": name, "sets": list(sets),
                            "p50": round(pct(ts, 0.5), 1), "p95": round(pct(ts, 0.95), 1),
                            "avg": round(st.mean(ts), 1), "workers": workers,
                            "identical": diff == 0})

    full = out["runs"][1]["p50"]
    print(f"\n  넷 다 걸었을 때 p50 {full:.1f}ms 를 기준으로,")
    for r in out["runs"][2:]:
        keep = "유지" if r["p50"] <= full * 1.1 else "효과 잃음"
        print(f"    {r['name']:<18s} p50 {r['p50']:>7.1f}ms  워커 {r['workers']}  {keep}")
    Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
