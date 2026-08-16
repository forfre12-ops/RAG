"""병렬 워커를 늘린 이득이 **동시 요청에서도 남는지** 잰다.

왜(2026-08-16). `tune_pg_parallel.py` 로 p50 190.5 -> 90.0ms 를 얻었다(결과 40/40 동일).
레버는 순차 스캔의 병렬 워커 수였다. 그런데 그 값은 **클라이언트 하나**로 잰 것이다.

    쿼리 하나가 워커 6개를 쓴다
    서버 전체 max_parallel_workers = 8
    호스트 CPU = 8

동시 요청이 둘만 돼도 워커가 모자라 뒷 쿼리는 적은 워커로 떨어진다(PostgreSQL 은
워커를 못 받으면 그만큼만 쓰고 진행한다 - 실패하지 않는다). 그래서 단일 측정값을
그대로 운영 수치로 쓰면 과장이다.

여기서는 동시 클라이언트 수를 1·2·4·8 로 올려가며 두 설정을 비교한다.

    기본     지금 서버 설정 그대로
    강제6    parallel_setup_cost=0 · parallel_tuple_cost=0 ·
             min_parallel_table_scan_size=0 · max_parallel_workers_per_gather=6

보는 값은 두 가지다. **응답시간**(사용자가 겪는 것)과 **처리량**(초당 완료 수).
워커를 늘리면 한 건은 빨라지지만 전체 처리량은 줄 수 있다 - 같은 일을 더 많은
프로세스가 나눠 하느라 조율 비용이 붙기 때문이다. 둘 다 봐야 판단이 선다.

⚠ 연결은 요청마다 새로 연다(출하 경로와 동일). 재사용하면 11회차부터 범용 계획으로
  바뀌어 4~8배 느려진다(diag_pg_conn_effect.py 실측).

⚠ 이 서버는 CPU 8개다. 운영 서버 사양이 다르면 값도 다르다.
"""
from __future__ import annotations

import argparse
import json
import os
import statistics as st
import sys
import threading
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

FORCE_SETS = (
    "SET parallel_setup_cost = 0",
    "SET parallel_tuple_cost = 0",
    "SET min_parallel_table_scan_size = 0",
)


def pct(xs: list[float], p: float) -> float:
    s = sorted(xs)
    return s[min(len(s) - 1, int(round((len(s) - 1) * p)))] if s else 0.0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="동시 요청에서의 병렬 이득")
    ap.add_argument("--collection", default="p2_cached_pg")
    ap.add_argument("--queries-file", default="datasets/gold_real/retrieval_gold.jsonl")
    ap.add_argument("--limit", type=int, default=40, help="쿼리 풀 크기")
    ap.add_argument("--per-client", type=int, default=15, help="클라이언트당 요청 수")
    ap.add_argument("--top-k", type=int, default=5)
    ap.add_argument("--report", default="reports/PG_CONCURRENCY.json")
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

    with vs._engine.connect() as c:  # noqa: SLF001
        mpw = c.execute(text("show max_parallel_workers")).scalar()
        mwp = c.execute(text("show max_worker_processes")).scalar()
    print(f"쿼리 풀 {len(prep)}건 · 클라이언트당 {args.per_client}회 · "
          f"max_parallel_workers={mpw} · max_worker_processes={mwp}\n")

    def one_client(setting: str, n: int, start: threading.Event,
                   out: list[float]) -> None:
        start.wait()
        for i in range(n):
            p = prep[i % len(prep)]
            with vs._engine.connect() as c:
                if setting == "force6":
                    for s in FORCE_SETS:
                        c.execute(text(s))
                    c.execute(text("SET max_parallel_workers_per_gather = 6"))
                t0 = time.perf_counter()
                c.execute(text(SQL_NOW), p).fetchall()
                out.append((time.perf_counter() - t0) * 1000)

    def run(setting: str, clients: int) -> dict:
        # 워밍업
        w: list[float] = []
        ev0 = threading.Event()
        ev0.set()
        one_client(setting, 3, ev0, w)

        buckets: list[list[float]] = [[] for _ in range(clients)]
        ev = threading.Event()
        threads = [threading.Thread(target=one_client,
                                    args=(setting, args.per_client, ev, buckets[i]))
                   for i in range(clients)]
        for t in threads:
            t.start()
        t0 = time.perf_counter()
        ev.set()
        for t in threads:
            t.join()
        wall = time.perf_counter() - t0
        allt = [x for b in buckets for x in b]
        return {"setting": setting, "clients": clients, "n": len(allt),
                "p50": round(pct(allt, 0.5), 1), "p95": round(pct(allt, 0.95), 1),
                "avg": round(st.mean(allt), 1),
                "qps": round(len(allt) / wall, 2), "wall_s": round(wall, 2)}

    out: dict = {"collection": args.collection, "max_parallel_workers": mpw, "runs": []}
    print(f"  {'설정':<8s} {'동시':>4s} {'p50':>9s} {'p95':>9s} {'평균':>9s} {'처리량':>10s}")
    for clients in (1, 2, 4, 8):
        for setting in ("기본", "force6"):
            r = run(setting, clients)
            out["runs"].append(r)
            print(f"  {setting:<8s} {clients:>4d} {r['p50']:>8.1f}ms {r['p95']:>8.1f}ms "
                  f"{r['avg']:>8.1f}ms {r['qps']:>7.2f}건/s")
        print()

    base1 = next(r for r in out["runs"] if r["setting"] == "기본" and r["clients"] == 1)
    print("판단 근거")
    for clients in (1, 2, 4, 8):
        b = next(r for r in out["runs"] if r["setting"] == "기본" and r["clients"] == clients)
        f = next(r for r in out["runs"] if r["setting"] == "force6" and r["clients"] == clients)
        d_p50 = f["p50"] - b["p50"]
        d_qps = f["qps"] - b["qps"]
        verdict = "이득" if (d_p50 < 0 and d_qps >= -0.5) else (
            "응답만 이득·처리량 손해" if d_p50 < 0 else "이득 없음")
        print(f"  동시 {clients}  응답 {d_p50:+7.1f}ms  처리량 {d_qps:+6.2f}건/s   {verdict}")
    print(f"\n  단일 클라이언트 기준값 p50 {base1['p50']:.1f}ms · 기준 200ms")
    Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    print("\n⚠ CPU 8개인 이 서버의 값이다. 운영 사양이 다르면 다시 재야 한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
