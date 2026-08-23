"""병렬 워커를 늘린 이득이 **동시 요청에서도 남는지** 잰다.

왜(2026-08-16). `tune_pg_parallel.py` 로 p50 190.5 -> 90.0ms 를 얻었다(결과 40/40 동일).
레버는 순차 스캔의 병렬 워커 수였다. 그런데 그 값은 **클라이언트 하나**로 잰 것이다.

    쿼리 하나가 워커 6개를 쓴다
    서버 전체 max_parallel_workers = 8
    호스트 CPU = 8

동시 요청이 둘만 돼도 워커가 모자라 뒷 쿼리는 적은 워커로 떨어진다(PostgreSQL 은
워커를 못 받으면 그만큼만 쓰고 진행한다 - 실패하지 않는다). 그래서 단일 측정값을
그대로 운영 수치로 쓰면 과장이다.

여기서는 동시 클라이언트 수를 1·2·4·8 로 올려가며 병렬 정도를 여러 값으로 비교한다.

    기본      지금 서버 설정 그대로(per_gather=2, 실제로 뜨는 워커도 2개)
    강제 N    parallel_setup_cost=0 · parallel_tuple_cost=0 ·
              min_parallel_table_scan_size=0 · max_parallel_workers_per_gather=N

⚠ 1차 측정(강제6만)에서 나온 것: 동시 1~2 에서는 p50 이 절반이 되지만 **꼬리(p95)가
  나빠진다** - 동시 2 에서 255 -> 317ms, 동시 4 에서 334 -> 551ms. 워커를 6개씩
  잡으면 먼저 온 쿼리가 다 가져가고 뒤 쿼리는 0개를 받아 편차가 커진다. 그래서
  중간 값(2·3·4)을 함께 재서 무릎점을 찾는다.

보는 값은 세 가지다. **p50**(보통 겪는 응답)·**p95**(느린 쪽이 얼마나 나쁜지)·
**처리량**(초당 완료 수). p50 만 보면 꼬리가 나빠지는 것을 놓친다.

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

CONCURRENCIES = (1, 2, 4, 8)
SETTINGS: list[tuple[str, int | None]] = [
    ("기본", None), ("강제2", 2), ("강제3", 3), ("강제4", 4), ("강제6", 6)]


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

    def one_client(degree: int | None, n: int, start: threading.Event,
                   out: list[float]) -> None:
        start.wait()
        for i in range(n):
            p = prep[i % len(prep)]
            with vs._engine.connect() as c:
                if degree is not None:
                    for s in FORCE_SETS:
                        c.execute(text(s))
                    c.execute(text(f"SET max_parallel_workers_per_gather = {degree}"))
                t0 = time.perf_counter()
                c.execute(text(SQL_NOW), p).fetchall()
                out.append((time.perf_counter() - t0) * 1000)

    def run(setting: str, degree: int | None, clients: int) -> dict:
        w: list[float] = []          # 워밍업
        ev0 = threading.Event()
        ev0.set()
        one_client(degree, 3, ev0, w)

        buckets: list[list[float]] = [[] for _ in range(clients)]
        ev = threading.Event()
        threads = [threading.Thread(target=one_client,
                                    args=(degree, args.per_client, ev, buckets[i]))
                   for i in range(clients)]
        for t in threads:
            t.start()
        t0 = time.perf_counter()
        ev.set()
        for t in threads:
            t.join()
        wall = time.perf_counter() - t0
        allt = [x for b in buckets for x in b]
        return {"setting": setting, "degree": degree, "clients": clients, "n": len(allt),
                "p50": round(pct(allt, 0.5), 1), "p95": round(pct(allt, 0.95), 1),
                "avg": round(st.mean(allt), 1),
                "qps": round(len(allt) / wall, 2), "wall_s": round(wall, 2)}

    out: dict = {"collection": args.collection, "max_parallel_workers": mpw, "runs": []}
    print(f"  {'설정':<7s} {'동시':>4s} {'p50':>9s} {'p95':>9s} {'평균':>9s} {'처리량':>10s}")
    for clients in CONCURRENCIES:
        for name, degree in SETTINGS:
            r = run(name, degree, clients)
            out["runs"].append(r)
            print(f"  {name:<7s} {clients:>4d} {r['p50']:>8.1f}ms {r['p95']:>8.1f}ms "
                  f"{r['avg']:>8.1f}ms {r['qps']:>7.2f}건/s")
        print()

    def pick(name: str, clients: int) -> dict:
        return next(r for r in out["runs"]
                    if r["setting"] == name and r["clients"] == clients)

    print("기본 대비 p50/p95/처리량 - p95 가 커지면 꼬리를 잃은 것이다")
    for clients in CONCURRENCIES:
        b = pick("기본", clients)
        line = f"  동시 {clients}  "
        for name, _ in SETTINGS[1:]:
            f = pick(name, clients)
            line += (f"{name} {f['p50'] - b['p50']:+5.0f}/{f['p95'] - b['p95']:+5.0f}"
                     f"/{f['qps'] - b['qps']:+4.1f}  ")
        print(line)

    print("\n둘 다 개선(p50·p95 모두 기본보다 낮음)한 조합")
    safe: dict[str, list[str]] = {}
    for clients in CONCURRENCIES:
        b = pick("기본", clients)
        got = [n for n, _ in SETTINGS[1:]
               if pick(n, clients)["p50"] < b["p50"] and pick(n, clients)["p95"] < b["p95"]]
        safe[str(clients)] = got
        print(f"  동시 {clients}  {', '.join(got) if got else '없음'}")

    # 모든 동시성에서 안전한 값이 있으면 그것이 무조건 채택 가능한 설정이다
    every = set(safe[str(CONCURRENCIES[0])])
    for c_ in CONCURRENCIES[1:]:
        every &= set(safe[str(c_)])
    out["safe_by_concurrency"] = safe
    out["safe_everywhere"] = sorted(every)
    print(f"\n  모든 동시성에서 안전: {', '.join(sorted(every)) if every else '없음'}")
    print(f"  단일 클라이언트 기본값 p50 {pick('기본', 1)['p50']:.1f}ms · 기준 200ms")

    Path(args.report).write_text(json.dumps(out, ensure_ascii=False, indent=2), "utf-8")
    print(f"\n[report] {args.report}")
    print("\n⚠ CPU 8개인 이 서버의 값이다. 운영 사양이 다르면 다시 재야 한다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
